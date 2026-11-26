import io
import json
import os
import time
from contextlib import redirect_stdout
from unittest import mock

from base import TmpHome, organizer

MB = 1024 * 1024
DAY = 86400


def fake_run_factory(responses, calls):
    def run(argv, **kw):
        calls.append(list(argv))
        for prefix, (code, out) in responses.items():
            if list(argv[: len(prefix)]) == list(prefix):
                return mock.Mock(returncode=code, stdout=out, stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")
    return run


class CleanupBase(TmpHome):
    def setUp(self):
        super().setUp()
        self.which = mock.patch.object(organizer, "_which", lambda name: None)
        self.which.start()

    def tearDown(self):
        self.which.stop()
        super().tearDown()

    def config(self, **cleanup):
        c = {"categories": list(organizer.CLEANUP_CATEGORIES), "min_size_mb": 1, "leftover_min_age_days": 30}
        c.update(cleanup)
        (self.home / ".organize").mkdir(exist_ok=True)
        (self.home / ".organize/config.json").write_text(json.dumps({"cleanup": c}))

    def blob(self, rel, size=2 * MB, age_days=0):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(size)
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))
        return p

    def ids(self):
        return {i["id"]: i for i in organizer.cleanup_scan()["items"]}


class MacCleanupTest(CleanupBase):
    PLATFORM = "macos"

    def test_off_by_default(self):
        self.blob("Library/Caches/com.foo.Big/data")
        self.assertEqual(organizer.cleanup_scan()["items"], [])

    def test_app_caches_and_min_size(self):
        self.config()
        big = self.blob("Library/Caches/com.foo.Big/data")
        self.blob("Library/Caches/com.foo.Small/data", size=10)
        self.blob("Library/Caches/com.apple.Safari/data")
        items = self.ids()
        self.assertIn(f"cache:{big.parent}", items)
        self.assertFalse(any("Small" in i or "com.apple" in i for i in items))

    def test_leftovers_vs_installed_apps(self):
        self.config()
        (self.home / "Applications/Spotify.app").mkdir(parents=True)
        self.blob("Library/Application Support/Spotify/cache.bin", age_days=400)
        old = self.blob("Library/Application Support/OldEditor/data.bin", age_days=400).parent
        self.blob("Library/Application Support/RecentThing/data.bin", age_days=2)
        self.blob("Library/Application Support/AddressBook/x", age_days=400)
        empty = self.home / "Library/Application Support/Ghost"
        empty.mkdir(parents=True)
        items = self.ids()
        self.assertIn(f"leftover:{old}", items)
        self.assertIn(f"empty:{empty}", items)
        for name in ("Spotify", "RecentThing", "AddressBook"):
            self.assertFalse(any(name in i for i in items), name)

    def test_cli_tools_count_as_installed(self):
        self.config()
        self.blob("Library/Application Support/ollama/models.bin", age_days=400)
        with mock.patch.object(organizer, "_which", lambda n: "/usr/bin/ollama" if n == "ollama" else None):
            self.assertFalse(any("ollama" in i for i in self.ids()))

    def test_apply_trashes_and_undo_restores(self):
        self.config()
        cache = self.blob("Library/Caches/com.foo.Big/data").parent
        run_id, res = organizer.cleanup_apply([f"cache:{cache}"])
        self.assertEqual(res[0]["status"], "ok", res)
        self.assertFalse(cache.exists())
        self.assertTrue(any((self.home / ".Trash").iterdir()))
        organizer.undo(run_id)
        self.assertTrue(cache.exists())

    def test_apply_refuses_ids_not_in_fresh_scan(self):
        self.config()
        secret = self.blob(".ssh/id_rsa")
        docs = self.blob("Documents/thesis.pdf")
        _, res = organizer.cleanup_apply([f"cache:{secret}", f"leftover:{docs}", "cache:/etc", "docker:volume:x"])
        self.assertTrue(all(r["status"] == "error" for r in res), res)
        self.assertTrue(secret.exists() and docs.exists())

    def test_protected_paths_respected(self):
        (self.home / ".organize").mkdir(exist_ok=True)
        (self.home / ".organize/config.json").write_text(json.dumps({
            "cleanup": {"categories": ["cache"], "min_size_mb": 1, "leftover_min_age_days": 0},
            "protected": ["~/Library/Caches/com.keep.Me"]}))
        self.blob("Library/Caches/com.keep.Me/data")
        self.assertFalse(any("com.keep.Me" in i for i in self.ids()))
        self.assertIsNotNone(organizer.check_cleanup_path(self.home / "Library/Caches/com.keep.Me"))

    def test_symlink_never_followed(self):
        self.config()
        target = self.blob("Documents/important.bin").parent
        link = self.home / "Library/Caches/link"
        link.parent.mkdir(parents=True, exist_ok=True)
        self.symlink(target, link)
        self.assertFalse(any("link" in i for i in self.ids()))
        self.assertIsNotNone(organizer.check_cleanup_path(link))


class DockerAndToolsTest(CleanupBase):
    PLATFORM = "linux"

    DF = "\n".join(json.dumps(r) for r in [
        {"Type": "Images", "TotalCount": "12", "Active": "3", "Size": "8.5GB", "Reclaimable": "6.1GB (71%)"},
        {"Type": "Containers", "TotalCount": "5", "Active": "1", "Size": "300MB", "Reclaimable": "250MB (83%)"},
        {"Type": "Local Volumes", "TotalCount": "2", "Active": "1", "Size": "1GB", "Reclaimable": "500MB"},
        {"Type": "Build Cache", "TotalCount": "40", "Active": "0", "Size": "2GB", "Reclaimable": "2GB"},
    ])

    def docker(self, calls, df=(0, None)):
        responses = {
            ("docker", "system", "df"): (df[0], df[1] if df[1] is not None else self.DF),
            ("docker", "images"): (0, "1.2GB\n300MB\n"),
            ("docker", "volume", "ls"): (0, "old_pgdata\n"),
        }
        return mock.patch.object(organizer, "_run", fake_run_factory(responses, calls))

    def test_docker_items(self):
        self.config(categories=["docker"])
        calls = []
        with mock.patch.object(organizer, "_which", lambda n: "/usr/bin/docker" if n == "docker" else None), \
                self.docker(calls):
            items = self.ids()
        self.assertEqual(items["docker:dangling-images"]["size"], 1500000000)
        self.assertEqual(items["docker:unused-images"]["command"], ["docker", "image", "prune", "-a", "-f"])
        self.assertIn("docker:build-cache", items)
        self.assertIn("docker:stopped-containers", items)
        self.assertEqual(items["docker:volume:old_pgdata"]["risk"], "high")

    def test_docker_not_running(self):
        self.config(categories=["docker"])
        with mock.patch.object(organizer, "_which", lambda n: "/usr/bin/docker" if n == "docker" else None), \
                self.docker([], df=(1, "")):
            items = organizer.cleanup_scan()["items"]
        self.assertEqual(items[0]["id"], "docker:error")

    def test_docker_apply_runs_only_known_commands(self):
        self.config(categories=["docker"])
        calls = []
        with mock.patch.object(organizer, "_which", lambda n: "/usr/bin/docker" if n == "docker" else None), \
                self.docker(calls):
            run_id, res = organizer.cleanup_apply(["docker:build-cache", "docker:volume:x; rm -rf ~"])
        self.assertEqual(res[0]["status"], "ok")
        self.assertEqual(res[1]["status"], "error")
        self.assertIn(["docker", "builder", "prune", "-f"], calls)
        self.assertFalse(any("rm" in call or any("~" in c for c in call) for call in calls))
        undone = organizer.undo(run_id)
        self.assertEqual(undone[0]["status"], "skipped")

    def test_tool_cache_uses_tool_command_and_is_not_double_counted(self):
        self.config(categories=["tool", "cache"])
        self.blob(".npm/_cacache/content/x")
        self.blob(".cache/pip/http/x")
        self.blob(".cache/thumbnails/x")
        which = lambda n: f"/usr/bin/{n}" if n in ("npm", "pip3") else None
        with mock.patch.object(organizer, "_which", which):
            items = self.ids()
        self.assertEqual(items["tool:npm"]["command"], ["npm", "cache", "clean", "--force"])
        self.assertEqual(items["tool:pip"]["command"], ["pip3", "cache", "purge"])
        self.assertIn(f"cache:{self.home / '.cache/thumbnails'}", items)
        self.assertNotIn(f"cache:{self.home / '.cache/pip'}", items)

    def test_linux_leftovers(self):
        self.config(categories=["leftover", "empty"])
        apps = self.home / ".local/share/applications"
        apps.mkdir(parents=True)
        (apps / "org.gimp.GIMP.desktop").write_text("[Desktop Entry]\nName=GIMP\nExec=gimp %U\n")
        self.blob(".config/GIMP/settings", age_days=400)
        old = self.blob(".config/oldchat/settings", age_days=400).parent
        self.blob(".config/gtk-3.0/settings.ini", age_days=400)
        items = self.ids()
        self.assertIn(f"leftover:{old}", items)
        self.assertFalse(any("GIMP" in i or "gtk-3.0" in i for i in items))

    def test_cli(self):
        self.config(categories=[])
        self.blob(".cache/thumbnails/x")
        buf = io.StringIO()
        with redirect_stdout(buf):
            organizer.main(["cleanup-scan", "--categories", "cache"])
        out = json.loads(buf.getvalue())
        ids = [i["id"] for i in out["items"]]
        self.assertEqual(len(ids), 1)
        # apply rescans the same categories even though the config has none enabled
        f = self.home / "ids.json"
        f.write_text(json.dumps(ids))
        buf = io.StringIO()
        with redirect_stdout(buf):
            organizer.main(["cleanup-apply", str(f)])
        self.assertEqual(json.loads(buf.getvalue())["results"][0]["status"], "ok")

    def test_bad_config(self):
        for bad in ({"categories": ["everything"]}, {"min_size_mb": -1}, {"x": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(organizer.ConfigError):
                    organizer.validate_config({"cleanup": {**{"categories": [], "min_size_mb": 1,
                                                              "leftover_min_age_days": 0}, **bad}})


class WindowsCleanupTest(CleanupBase):
    PLATFORM = "windows"

    def test_temp_only_when_old(self):
        self.config(categories=["cache"])
        old = self.blob("AppData/Local/Temp/old_setup/x.bin", age_days=30).parent
        self.blob("AppData/Local/Temp/fresh/x.bin")
        items = self.ids()
        self.assertIn(f"cache:{old}", items)
        self.assertFalse(any("fresh" in i for i in items))

    def test_leftovers_vs_program_files(self):
        self.config(categories=["leftover"])
        (self.home / "Program Files/Notepad++").mkdir(parents=True)
        self.blob("AppData/Roaming/Notepad++/config.xml", age_days=400)
        old = self.blob("AppData/Roaming/OldGame/save.dat", age_days=400).parent
        self.blob("AppData/Roaming/Microsoft/x", age_days=400)
        items = self.ids()
        self.assertIn(f"leftover:{old}", items)
        self.assertFalse(any("Notepad" in i or "Microsoft" in i for i in items))
