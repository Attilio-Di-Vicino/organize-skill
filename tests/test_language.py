import io
import json
import os
from contextlib import redirect_stdout
from unittest import mock

from base import TmpHome, organizer


class SystemLanguageTest(TmpHome):
    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.pop(k, None) for k in
                      ("ORGANIZE_SYSTEM_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")}

    def tearDown(self):
        for k in ("ORGANIZE_SYSTEM_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            os.environ.pop(k, None)
            if self.saved[k] is not None:
                os.environ[k] = self.saved[k]
        super().tearDown()

    def test_default_naming_follows_system(self):
        self.assertEqual(organizer.load_config()["naming"]["language"], "system")
        os.environ["ORGANIZE_SYSTEM_LANG"] = "it-IT"
        self.assertEqual(organizer.naming_language(organizer.load_config()), "it")
        os.environ["ORGANIZE_SYSTEM_LANG"] = "en_US.UTF-8"
        self.assertEqual(organizer.naming_language(organizer.load_config()), "en")

    def test_fixed_language_ignores_system(self):
        os.environ["ORGANIZE_SYSTEM_LANG"] = "it-IT"
        cfg = organizer.validate_config({"naming": {"language": "en"}})
        self.assertEqual(organizer.naming_language(cfg), "en")

    def test_macos_apple_languages(self):
        out = '(\n    "it-IT",\n    "en-US"\n)\n'
        run = lambda *a, **k: mock.Mock(stdout=out)
        with mock.patch.object(organizer, "_run", run):
            self.assertEqual(organizer.system_language()["code"], "it")

    def test_linux_env_order_and_c_locale(self):
        os.environ["ORGANIZE_PLATFORM"] = "linux"
        os.environ["LANG"] = "de_DE.UTF-8"
        self.assertEqual(organizer.system_language()["code"], "de")
        os.environ["LANGUAGE"] = "it:en"
        self.assertEqual(organizer.system_language()["code"], "it")
        os.environ["LANGUAGE"] = ""
        os.environ["LC_ALL"] = "C"
        self.assertEqual(organizer.system_language()["code"], "de")

    def test_info_reports_it(self):
        os.environ["ORGANIZE_SYSTEM_LANG"] = "it-IT"
        buf = io.StringIO()
        with redirect_stdout(buf):
            organizer.main(["info"])
        info = json.loads(buf.getvalue())
        self.assertEqual(info["naming_language"], "it")
        self.assertEqual(info["system_language"]["tag"], "it-IT")

    def test_bad_language_rejected(self):
        for bad in ("Italian", "", "it it", 3):
            with self.subTest(bad=bad):
                with self.assertRaises(organizer.ConfigError):
                    organizer.validate_config({"naming": {"language": bad}})
