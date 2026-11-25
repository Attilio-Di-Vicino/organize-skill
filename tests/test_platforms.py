import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from base import TmpHome, organizer


class LinuxTest(TmpHome):
    PLATFORM = "linux"

    def trash(self):
        return self.home / ".local/share/Trash"

    def test_xdg_localized_folders(self):
        (self.home / "Scrivania").mkdir()
        self.touch(".config/user-dirs.dirs",
                   '# comment\nXDG_DESKTOP_DIR="$HOME/Scrivania"\nXDG_DOWNLOAD_DIR="$HOME/Scaricati"\n')
        f = organizer.user_folders()
        self.assertEqual(f["desktop"], self.home / "Scrivania")
        self.assertEqual(f["downloads"], self.home / "Scaricati")
        self.assertEqual(f["documents"], self.home / "Documents")  # fallback

    def test_trash_writes_trashinfo(self):
        src = self.touch("Desktop/my file.txt")
        _, res = organizer.apply_plan([{"op": "trash", "src": str(src)}])
        self.assertEqual(res[0]["status"], "ok")
        self.assertTrue((self.trash() / "files/my file.txt").exists())
        info = (self.trash() / "info/my file.txt.trashinfo").read_text()
        self.assertIn("[Trash Info]", info)
        self.assertIn("Path=" + quote(str(src.resolve()), safe="/"), info)
        self.assertRegex(info, r"DeletionDate=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    def test_trash_name_clash_checks_info_too(self):
        (self.trash() / "info").mkdir(parents=True)
        (self.trash() / "info/a.txt.trashinfo").write_text("[Trash Info]\n")
        organizer.apply_plan([{"op": "trash", "src": str(self.touch("Desktop/a.txt"))}])
        self.assertTrue((self.trash() / "files/a (2).txt").exists())
        self.assertTrue((self.trash() / "info/a (2).txt.trashinfo").exists())

    def test_undo_restores_and_removes_trashinfo(self):
        src = self.touch("Desktop/a.txt")
        run_id, _ = organizer.apply_plan([{"op": "trash", "src": str(src)}])
        organizer.undo(run_id)
        self.assertTrue(src.exists())
        self.assertFalse((self.trash() / "info/a.txt.trashinfo").exists())

    def test_list_shows_original_path_and_empty_clears(self):
        src = self.touch("Desktop/a.txt")
        organizer.apply_plan([{"op": "trash", "src": str(src)}])
        items = organizer.trash_backend().list()
        self.assertEqual(items[0]["original_path"], str(src.resolve()))
        organizer.trash_backend().empty()
        self.assertEqual(os.listdir(self.trash() / "files"), [])
        self.assertEqual(os.listdir(self.trash() / "info"), [])

    def test_systemd_job_detected(self):
        proj = self.home / "Documents/bot"
        (proj / ".venv").mkdir(parents=True)
        (proj / ".venv/pyvenv.cfg").write_text("home = /usr/bin")
        self.touch(".config/systemd/user/bot.service", f"ExecStart={proj.resolve()}/run.sh\n")
        r = organizer.check_project(proj)
        self.assertFalse(r["safe_to_move"])
        self.assertEqual(len(r["venvs"]), 1)
        self.assertTrue(any("systemd" in j for j in r["scheduled_jobs"]))


class MacProjectTest(TmpHome):
    PLATFORM = "macos"

    def test_launchd_job_detected(self):
        proj = self.home / "Documents/trader"
        proj.mkdir()
        self.touch("Library/LaunchAgents/com.trader.plist", f"<string>{proj.resolve()}/main.py</string>")
        r = organizer.check_project(proj)
        self.assertFalse(r["safe_to_move"])
        self.assertTrue(any("launchd" in j for j in r["scheduled_jobs"]))

    def test_plain_project_is_safe(self):
        proj = self.home / "Documents/site"
        (proj / "node_modules/x").mkdir(parents=True)
        self.assertTrue(organizer.check_project(proj)["safe_to_move"])


class WindowsMockTest(TmpHome):
    PLATFORM = "windows"

    def fake_run(self, stdout=""):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return calls, run

    def test_trash_calls_recycle_bin(self):
        src = self.touch("Desktop/a.txt")
        calls, run = self.fake_run()
        with mock.patch.object(organizer, "_run", run):
            run_id, res = organizer.apply_plan([{"op": "trash", "src": str(src)}])
        self.assertEqual(res[0]["status"], "ok")
        cmd, kw = calls[0]
        self.assertEqual(cmd[0], "powershell")
        self.assertIn("DeleteFile", cmd[-1])
        self.assertIn("SendToRecycleBin", cmd[-1])
        self.assertEqual(kw["env"]["ORGANIZE_TARGET"], str(src.resolve()))
        res = organizer.undo(run_id)
        self.assertEqual(res[0]["status"], "skipped")
        self.assertIn("Recycle Bin", res[0]["msg"])

    def test_trash_folder_uses_delete_directory(self):
        d = self.home / "Desktop/Folder"
        d.mkdir()
        calls, run = self.fake_run()
        with mock.patch.object(organizer, "_run", run):
            organizer.apply_plan([{"op": "trash", "src": str(d)}])
        self.assertIn("DeleteDirectory", calls[0][0][-1])

    def test_list_handles_single_object(self):
        one = json.dumps({"name": "a.txt", "path": "C:\\$Recycle.Bin\\a.txt", "is_dir": False, "size": 1})
        _, run = self.fake_run(stdout=one)
        with mock.patch.object(organizer, "_run", run):
            self.assertEqual(len(organizer.trash_backend().list()), 1)
        _, run = self.fake_run(stdout="")
        with mock.patch.object(organizer, "_run", run):
            self.assertEqual(organizer.trash_backend().list(), [])

    def test_powershell_failure_reported_as_error(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Access denied")
        src = self.touch("Desktop/a.txt")
        with mock.patch.object(organizer, "_run", run):
            _, res = organizer.apply_plan([{"op": "trash", "src": str(src)}])
        self.assertEqual(res[0]["status"], "error")
        self.assertTrue(src.exists())


@unittest.skipUnless(sys.platform.startswith("win") and os.environ.get("CI"), "real Recycle Bin, Windows CI only")
class WindowsRealTest(unittest.TestCase):
    def test_real_recycle_bin_and_folders(self):
        os.environ.pop("ORGANIZE_HOME", None)
        os.environ.pop("ORGANIZE_PLATFORM", None)
        folders = organizer.user_folders()
        self.assertTrue(folders["documents"].exists(), folders)
        with tempfile.TemporaryDirectory(dir=str(folders["documents"])) as t:
            f = Path(t) / "organize-ci-test.txt"
            f.write_text("x")
            _, res = organizer.apply_plan([{"op": "trash", "src": str(f)}])
            self.assertEqual(res[0]["status"], "ok", res)
            self.assertFalse(f.exists())


@unittest.skipUnless(sys.platform.startswith("linux") and os.environ.get("CI"), "real home, Linux CI only")
class LinuxRealTest(unittest.TestCase):
    def test_real_trash(self):
        os.environ.pop("ORGANIZE_HOME", None)
        os.environ.pop("ORGANIZE_PLATFORM", None)
        docs = organizer.user_folders()["documents"]
        docs.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(docs)) as t:
            f = Path(t) / "organize-ci-test.txt"
            f.write_text("x")
            _, res = organizer.apply_plan([{"op": "trash", "src": str(f)}])
            self.assertEqual(res[0]["status"], "ok", res)
            self.assertTrue(any(i["name"] == res[0]["dst"].split("/")[-1]
                                for i in organizer.trash_backend().list()))


if __name__ == "__main__":
    unittest.main()
