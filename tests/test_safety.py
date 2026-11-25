import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import TmpHome, organizer


class PathRulesTest(TmpHome):
    def refused(self, action):
        _, res = organizer.apply_plan([action])
        self.assertEqual(res[0]["status"], "error", res)
        self.assertIn("refused", res[0]["msg"])

    def test_source_outside_user_folders_refused(self):
        secret = self.touch(".ssh/id_rsa")
        self.refused({"op": "trash", "src": str(secret)})
        self.refused({"op": "move", "src": str(secret), "dst": str(self.home / "Documents/k")})
        self.assertTrue(secret.exists())

    def test_user_folder_itself_refused(self):
        self.refused({"op": "trash", "src": str(self.home / "Documents")})
        self.assertTrue((self.home / "Documents").exists())

    def test_destination_outside_user_folders_refused(self):
        src = self.touch("Desktop/a.txt")
        self.refused({"op": "move", "src": str(src), "dst": str(self.home / "Library/a.txt")})
        self.refused({"op": "mkdir", "dst": str(self.home / "Elsewhere")})
        self.assertTrue(src.exists())

    def test_hidden_and_cloud_roots_refused(self):
        self.touch("Documents/.git/config")
        (self.home / "Documents/OneDrive - Contoso").mkdir()
        self.refused({"op": "trash", "src": str(self.home / "Documents/.git")})
        self.refused({"op": "trash", "src": str(self.home / "Documents/OneDrive - Contoso")})

    def test_dotdot_escape_refused(self):
        secret = self.touch("secret.txt")
        self.refused({"op": "trash", "src": str(self.home / "Desktop/../secret.txt")})
        self.assertTrue(secret.exists())

    def test_skill_folder_and_installed_skills_protected(self):
        tool = self.home / "Documents/Tools/organize"
        (tool / "scripts").mkdir(parents=True)
        with mock.patch.object(organizer, "skill_dir", lambda: tool.resolve()):
            self.refused({"op": "trash", "src": str(self.home / "Documents/Tools")})
            self.refused({"op": "move", "src": str(tool), "dst": str(self.home / "Documents/x")})
        other = self.home / "Documents/my-skills/career"
        other.mkdir(parents=True)
        (self.home / ".claude/skills").mkdir(parents=True)
        os.symlink(str(other), str(self.home / ".claude/skills/career"))
        self.refused({"op": "move", "src": str(self.home / "Documents/my-skills"),
                      "dst": str(self.home / "Documents/Archive/my-skills")})


class SymlinkTest(TmpHome):
    PLATFORM = "linux"

    def test_trashing_a_link_keeps_its_target(self):
        target = self.touch("Documents/Big/data.bin")
        link = self.home / "Desktop/Big"
        os.symlink(str(target.parent), str(link))
        _, res = organizer.apply_plan([{"op": "trash", "src": str(link)}])
        self.assertEqual(res[0]["status"], "ok", res)
        self.assertTrue(target.exists())
        info = (self.home / ".local/share/Trash/info/Big.trashinfo").read_text()
        self.assertIn("/Desktop/Big", info)


class WindowsSymlinkTest(TmpHome):
    PLATFORM = "windows"

    def test_link_removed_without_recycling_target_and_undo_recreates(self):
        target = self.touch("Documents/Big/data.bin")
        link = self.home / "Desktop/Big"
        os.symlink(str(target.parent), str(link), target_is_directory=True)
        called = []
        with mock.patch.object(organizer, "_run", lambda *a, **k: called.append(a)):
            run_id, res = organizer.apply_plan([{"op": "trash", "src": str(link)}])
        self.assertEqual(res[0]["status"], "ok", res)
        self.assertEqual(called, [])  # PowerShell never ran
        self.assertFalse(link.is_symlink())
        self.assertTrue(target.exists())
        organizer.undo(run_id)
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
