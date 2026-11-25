import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "organize" / "scripts" / "organizer.py"
sys.path.insert(0, str(SCRIPT.parent))
import organizer  # noqa: E402,F401


class TmpHome(unittest.TestCase):
    """Runs each test against a throwaway home folder and a forced platform."""

    PLATFORM = "macos"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        os.environ["ORGANIZE_HOME"] = str(self.home)
        os.environ["ORGANIZE_PLATFORM"] = self.PLATFORM
        for d in ("Desktop", "Downloads", "Documents"):
            (self.home / d).mkdir()

    def tearDown(self):
        os.environ.pop("ORGANIZE_HOME", None)
        os.environ.pop("ORGANIZE_PLATFORM", None)
        self.tmp.cleanup()

    def touch(self, rel, text="x"):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p
