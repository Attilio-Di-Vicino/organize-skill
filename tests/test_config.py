import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from base import TmpHome, organizer


class ConfigTest(TmpHome):
    def write_config(self, cfg):
        d = self.home / ".organize"
        d.mkdir(exist_ok=True)
        (d / "config.json").write_text(json.dumps(cfg))

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = organizer.main(list(argv))
        return code, json.loads(buf.getvalue())

    def status(self, action):
        _, res = organizer.apply_plan([action])
        return res[0]

    # defaults

    def test_no_config_means_original_behaviour(self):
        cfg = organizer.load_config()
        self.assertEqual({k: v["policy"] for k, v in cfg["folders"].items()},
                         {"desktop": "empty", "downloads": "empty", "documents": "tidy"})
        self.assertEqual(cfg["trash"], "ask")
        names = [n for n, _, _ in organizer.managed_roots()]
        self.assertEqual(names, ["desktop", "downloads", "documents"])

    def test_partial_config_merges_with_defaults(self):
        self.write_config({"folders": {"desktop": "keep"}})
        cfg = organizer.load_config()
        self.assertEqual(cfg["folders"]["desktop"]["policy"], "keep")
        self.assertEqual(cfg["folders"]["downloads"]["policy"], "empty")
        self.assertEqual(cfg["naming"]["case"], "Title Case")

    # validation

    def test_invalid_values_rejected(self):
        for bad in ({"folders": {"desktop": "nuke"}}, {"trash": "always"}, {"nope": 1},
                    {"max_depth": 0}, {"keep": "x"}, {"extra_folders": ["/etc"]},
                    {"extra_folders": ["~"]}, {"extra_folders": ["~/.ssh"]},
                    {"folders": {"music": "tidy"}},
                    {"folders": {"desktop": {"policy": "empty", "sections": ["A"]}}},
                    {"folders": {"desktop": {"policy": "tidy", "sections": ["../x"]}}},
                    {"folders": {"documents": "skip"}}):
            with self.subTest(bad=bad):
                with self.assertRaises(organizer.ConfigError):
                    organizer.validate_config(bad)

    def test_broken_config_blocks_operations(self):
        (self.home / ".organize").mkdir()
        (self.home / ".organize/config.json").write_text("{not json")
        src = self.touch("Desktop/a.txt")
        res = self.status({"op": "trash", "src": str(src)})
        self.assertEqual(res["status"], "error")
        self.assertIn("config error", res["msg"])
        self.assertTrue(src.exists())
        code, out = self.run_cli("scan")
        self.assertEqual(code, 3)
        self.assertEqual(out["error"], "config")

    # enforcement

    def test_skipped_folder_is_off_limits(self):
        self.write_config({"folders": {"desktop": "skip"}})
        src = self.touch("Desktop/a.txt")
        res = self.status({"op": "move", "src": str(src), "dst": str(self.home / "Documents/a.txt")})
        self.assertEqual(res["status"], "error")
        self.assertTrue(src.exists())
        code, items = self.run_cli("scan", "--no-metadata")
        self.assertFalse(any(i["location"] == "Desktop" for i in items))

    def test_extra_folder_is_managed(self):
        self.write_config({"extra_folders": [{"path": "~/Pictures", "policy": "tidy"}]})
        src = self.touch("Pictures/cat.jpg")
        res = self.status({"op": "move", "src": str(src), "dst": str(self.home / "Pictures/Pets/cat.jpg")})
        self.assertEqual(res["status"], "ok", res)
        self.assertTrue((self.home / "Pictures/Pets/cat.jpg").exists())
        _, items = self.run_cli("scan", "--no-metadata")
        pics = [i for i in items if i["location"] == "Pictures"]
        self.assertEqual(pics[0]["policy"], "tidy")

    def test_unconfigured_folder_still_refused(self):
        src = self.touch("Pictures/cat.jpg")
        res = self.status({"op": "trash", "src": str(src)})
        self.assertEqual(res["status"], "error")

    def test_protected_path(self):
        self.write_config({"protected": ["~/Desktop/Work In Progress"]})
        inner = self.touch("Desktop/Work In Progress/draft.docx")
        for action in ({"op": "trash", "src": str(inner)},
                       {"op": "trash", "src": str(inner.parent)},
                       {"op": "move", "src": str(self.touch("Desktop/x.txt")), "dst": str(inner.parent / "x.txt")}):
            with self.subTest(action=action):
                self.assertEqual(self.status(action)["status"], "error")
        self.assertTrue(inner.exists())

    def test_keep_patterns(self):
        self.write_config({"keep": ["*.lnk", "inbox"]})
        link = self.touch("Desktop/App.lnk")
        inbox_file = self.touch("Desktop/Inbox/todo.txt")
        other = self.touch("Desktop/other.txt")
        self.assertEqual(self.status({"op": "trash", "src": str(link)})["status"], "error")
        self.assertEqual(self.status({"op": "trash", "src": str(inbox_file)})["status"], "error")
        self.assertEqual(self.status({"op": "trash", "src": str(other)})["status"], "ok")

    def test_duplicate_keeper_follows_destination(self):
        self.write_config({"extra_folders": ["~/Archive"], "destination": "~/Archive"})
        self.touch("Downloads/a.pdf", "same")
        kept = self.touch("Archive/a.pdf", "same")
        items = organizer.scan([self.home / "Downloads", self.home / "Archive"], metadata=False)
        dup = next(i for i in items if i["location"] == "Downloads")
        self.assertIn("duplicate", dup["flags"])
        self.assertEqual(dup["duplicate_of"], str(kept))

    # CLI

    def test_config_init_and_write(self):
        code, out = self.run_cli("config", "--init")
        self.assertEqual(code, 0)
        self.assertTrue((self.home / ".organize/config.json").exists())
        code, out = self.run_cli("config", "--init")
        self.assertEqual(code, 1)

        new = self.home / "new.json"
        new.write_text(json.dumps({"trash": "never", "folders": {"desktop": "keep"}}))
        code, out = self.run_cli("config", "--write", str(new))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["config"]["trash"], "never")
        self.assertTrue((self.home / ".organize/config.json.bak").exists())

        new.write_text(json.dumps({"trash": "sometimes"}))
        code, out = self.run_cli("config", "--write", str(new))
        self.assertEqual(code, 1)
        code, out = self.run_cli("config")
        self.assertEqual(out["config"]["trash"], "never")

    def test_env_override_path(self):
        alt = self.home / "alt.json"
        alt.write_text(json.dumps({"trash": "never"}))
        os.environ["ORGANIZE_CONFIG"] = str(alt)
        try:
            self.assertEqual(organizer.load_config()["trash"], "never")
        finally:
            os.environ.pop("ORGANIZE_CONFIG")
