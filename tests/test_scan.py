import os
import subprocess
import sys
import unittest

from base import SCRIPT, TmpHome, organizer


class ScanTest(TmpHome):
    def item(self, items, name):
        return next(i for i in items if i["name"] == name)

    def test_zip_next_to_folder_flagged(self):
        desk = self.home / "Desktop"
        (desk / "Thesis").mkdir()
        (desk / "Thesis.zip").write_bytes(b"PK")
        items = organizer.scan([desk])
        self.assertIn("archive_extracted", self.item(items, "Thesis.zip")["flags"])

    def test_code_project_flagged(self):
        (self.home / "Desktop/proj/.git").mkdir(parents=True)
        items = organizer.scan([self.home / "Desktop"])
        self.assertIn("code_project", self.item(items, "proj")["flags"])

    def test_duplicate_keeps_documents_copy(self):
        self.touch("Desktop/a.pdf", "same")
        self.touch("Documents/a copy.pdf", "same")
        items = organizer.scan([self.home / "Desktop", self.home / "Documents"])
        self.assertIn("duplicate", self.item(items, "a.pdf")["flags"])
        self.assertNotIn("duplicate", self.item(items, "a copy.pdf")["flags"])

    def test_installers_for_every_os_flagged(self):
        for n in ("App.dmg", "Setup.exe", "tool.deb", "Thing.AppImage", "pkg.msi"):
            self.touch(f"Downloads/{n}")
        items = organizer.scan([self.home / "Downloads"])
        self.assertTrue(all("installer" in i["flags"] for i in items))

    def test_temp_files_flagged_and_not_duplicates(self):
        self.touch("Downloads/~$Report.docx", "lock")
        self.touch("Downloads/~$Other.docx", "lock")
        self.touch("Downloads/movie.mp4.crdownload")
        items = organizer.scan([self.home / "Downloads"])
        self.assertTrue(all("temp" in i["flags"] for i in items))
        self.assertFalse(any("duplicate" in i["flags"] for i in items))

    def test_screenshot_flagged(self):
        self.touch("Desktop/Screenshot 2026-06-24 at 19.49.20.png")
        self.touch("Desktop/Screenshot_20260624_194920.png")
        items = organizer.scan([self.home / "Desktop"])
        self.assertTrue(all("screenshot" in i["flags"] for i in items))

    def test_hidden_and_system_files_ignored(self):
        for n in (".DS_Store", "desktop.ini", "Thumbs.db", ".hidden"):
            self.touch(f"Desktop/{n}")
        self.assertEqual(organizer.scan([self.home / "Desktop"]), [])

    def test_missing_folder_is_ignored(self):
        self.assertEqual(organizer.scan([self.home / "Nope"]), [])


class CliTest(TmpHome):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env=dict(os.environ))

    def test_empty_trash_requires_flag(self):
        self.touch(".Trash/old.txt")
        r = self.run_cli("empty-trash")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.home / ".Trash/old.txt").exists())
        r = self.run_cli("empty-trash", "--yes-really")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / ".Trash/old.txt").exists())

    def test_info_reports_folders(self):
        r = self.run_cli("info")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"platform": "macos"', r.stdout)
        self.assertIn("Desktop", r.stdout)


if __name__ == "__main__":
    unittest.main()
