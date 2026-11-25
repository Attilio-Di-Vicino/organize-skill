import unittest

from base import TmpHome, organizer


class OpsTest(TmpHome):
    def test_move_creates_parent(self):
        src = self.touch("Desktop/a.txt")
        dst = self.home / "Documents/Finance/Bills/a.txt"
        _, res = organizer.apply_plan([{"op": "move", "src": str(src), "dst": str(dst)}])
        self.assertEqual(res[0]["status"], "ok")
        self.assertTrue(dst.exists())
        self.assertFalse(src.exists())

    def test_name_clash_appends_suffix(self):
        src = self.touch("Desktop/a.txt", "new")
        self.touch("Documents/a.txt", "old")
        _, res = organizer.apply_plan(
            [{"op": "move", "src": str(src), "dst": str(self.home / "Documents/a.txt")}])
        self.assertEqual(res[0]["status"], "ok")
        self.assertEqual((self.home / "Documents/a.txt").read_text(), "old")
        self.assertEqual((self.home / "Documents/a (2).txt").read_text(), "new")

    def test_missing_src_skipped(self):
        _, res = organizer.apply_plan([
            {"op": "move", "src": str(self.home / "Desktop/nope"), "dst": str(self.home / "Documents/x")},
            {"op": "move", "src": str(self.touch("Desktop/b.txt")), "dst": str(self.home / "Documents/b.txt")},
        ])
        self.assertEqual(res[0]["status"], "skipped")
        self.assertEqual(res[1]["status"], "ok")

    def test_move_into_itself_error(self):
        d = self.home / "Desktop/Folder"
        d.mkdir()
        _, res = organizer.apply_plan([{"op": "move", "src": str(d), "dst": str(d / "Sub/Folder")}])
        self.assertEqual(res[0]["status"], "error")
        self.assertTrue(d.exists())

    def test_unknown_op_is_error_not_crash(self):
        _, res = organizer.apply_plan([{"op": "delete", "src": str(self.touch("Desktop/a.txt"))}])
        self.assertEqual(res[0]["status"], "error")
        self.assertTrue((self.home / "Desktop/a.txt").exists())

    def test_undo_restores_and_removes_created_folders(self):
        src = self.touch("Desktop/a.txt")
        run_id, _ = organizer.apply_plan(
            [{"op": "move", "src": str(src), "dst": str(self.home / "Documents/New/a.txt")}])
        res = organizer.undo(run_id)
        self.assertTrue(all(r["status"] == "ok" for r in res))
        self.assertTrue(src.exists())
        self.assertFalse((self.home / "Documents/New").exists())

    def test_undo_skips_items_moved_again(self):
        a = self.touch("Desktop/a.txt")
        b = self.touch("Desktop/b.txt")
        run_id, _ = organizer.apply_plan([
            {"op": "move", "src": str(a), "dst": str(self.home / "Documents/a.txt")},
            {"op": "move", "src": str(b), "dst": str(self.home / "Documents/b.txt")},
        ])
        (self.home / "Documents/a.txt").rename(self.home / "Documents/moved.txt")
        res = organizer.undo(run_id)
        self.assertIn("skipped", [r["status"] for r in res])
        self.assertTrue(b.exists())

    def test_undo_defaults_to_last_run(self):
        a = self.touch("Desktop/a.txt")
        organizer.apply_plan([{"op": "move", "src": str(a), "dst": str(self.home / "Documents/a.txt")}])
        organizer.undo()
        self.assertTrue(a.exists())
        self.assertEqual(organizer.list_runs(), [])


class MacTrashTest(TmpHome):
    PLATFORM = "macos"

    def test_trash_moves_into_dot_trash_and_undo_restores(self):
        src = self.touch("Desktop/junk.zip")
        run_id, res = organizer.apply_plan([{"op": "trash", "src": str(src)}])
        self.assertEqual(res[0]["status"], "ok")
        self.assertTrue((self.home / ".Trash/junk.zip").exists())
        organizer.undo(run_id)
        self.assertTrue(src.exists())

    def test_trash_name_clash(self):
        self.touch(".Trash/junk.zip", "old")
        organizer.apply_plan([{"op": "trash", "src": str(self.touch("Desktop/junk.zip", "new"))}])
        self.assertEqual((self.home / ".Trash/junk (2).zip").read_text(), "new")


if __name__ == "__main__":
    unittest.main()
