from pathlib import Path
import io
import json
import os
import time
from contextlib import redirect_stdout

from base import TmpHome, organizer

MB = 1024 * 1024
DAY = 86400


class ReportTest(TmpHome):
    def write_config(self, cfg):
        (self.home / ".organize").mkdir(exist_ok=True)
        (self.home / ".organize/config.json").write_text(json.dumps(cfg))

    def sized(self, rel, size, age_days=0):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(size)
        t = time.time() - age_days * DAY
        os.utime(p, (t, t))
        return p

    def test_scan_flags_large_and_stale(self):
        self.write_config({"archive": {"large_mb": 1, "stale_days": 30}})
        self.sized("Downloads/big.iso", 2 * MB)
        self.sized("Downloads/old.pdf", 10, age_days=100)
        self.sized("Downloads/new.pdf", 10)
        items = {i["name"]: i for i in organizer.scan([self.home / "Downloads"], metadata=False)}
        self.assertIn("large", items["big.iso"]["flags"])
        self.assertNotIn("stale", items["big.iso"]["flags"])
        self.assertIn("stale", items["old.pdf"]["flags"])
        self.assertNotIn("stale", items["new.pdf"]["flags"])
        self.assertTrue(items["old.pdf"]["last_used"])

    def test_folder_with_recent_file_is_not_stale(self):
        self.write_config({"archive": {"large_mb": 0, "stale_days": 30}})
        self.sized("Documents/Taxes/2019.pdf", 10, age_days=900)
        self.sized("Documents/Taxes/2026.pdf", 10)
        d = self.home / "Documents/Taxes"
        os.utime(d, (time.time() - 900 * DAY,) * 2)
        items = {i["name"]: i for i in organizer.scan([self.home / "Documents"], metadata=False)}
        self.assertNotIn("stale", items["Taxes"]["flags"])

    def test_zero_disables(self):
        self.write_config({"archive": {"large_mb": 0, "stale_days": 0}})
        self.sized("Downloads/big.iso", 2 * MB, age_days=1000)
        item = organizer.scan([self.home / "Downloads"], metadata=False)[0]
        self.assertFalse({"large", "stale"} & set(item["flags"]))

    def test_report_walks_deep_but_respects_rules(self):
        self.write_config({"archive": {"top": 2, "stale_days": 30, "large_mb": 1},
                           "protected": ["~/Documents/Private"]})
        self.sized("Documents/a/b/c/huge.mov", 5 * MB, age_days=400)
        self.sized("Documents/mid.zip", 3 * MB)
        self.sized("Documents/small.txt", 10)
        self.sized("Documents/Private/secret.bin", 9 * MB)
        self.sized("Documents/.hidden/x.bin", 9 * MB)
        self.sized("Documents/app/node_modules/dep.bin", 4 * MB)
        (self.home / "Documents/app/package.json").write_text("{}")
        r = organizer.report()
        paths = [e["path"] for e in r["biggest"]]
        self.assertEqual(len(paths), 2)
        self.assertTrue(paths[0].endswith("huge.mov"))
        self.assertTrue(paths[1].endswith("app"))            # code project counted whole
        self.assertFalse(any("secret" in p or "hidden" in p or "node_modules" in p for p in paths))
        self.assertEqual([e["path"] for e in r["biggest_stale"]], [paths[0]])

    def test_protected_file_not_reported(self):
        self.write_config({"archive": {"top": 5, "large_mb": 1}, "protected": ["~/Documents/keep.bin"]})
        self.sized("Documents/keep.bin", 9 * MB)
        self.sized("Documents/other.bin", 2 * MB)
        paths = [e["path"] for e in organizer.report()["biggest"]]
        self.assertEqual([Path(x).name for x in paths], ["other.bin"])

    def test_skipped_folder_not_reported(self):
        self.write_config({"folders": {"downloads": "skip"}})
        self.sized("Downloads/big.iso", 5 * MB)
        self.assertEqual(organizer.report()["biggest"], [])

    def test_bad_archive_config(self):
        for bad in ({"archive": {"large_mb": -1}}, {"archive": {"top": 0}}, {"archive": {"x": 1}}):
            with self.subTest(bad=bad):
                with self.assertRaises(organizer.ConfigError):
                    organizer.validate_config(bad)


class RenameTest(TmpHome):
    def write_config(self, cfg):
        (self.home / ".organize").mkdir(exist_ok=True)
        (self.home / ".organize/config.json").write_text(json.dumps(cfg))

    def test_unclear_name_flag(self):
        for n in ("IMG_1234.jpg", "document (3).pdf", "scan0001.pdf", "Untitled.docx"):
            self.touch(f"Downloads/{n}")
        for n in ("Thesis v2.pdf", "CV Mario Rossi.pdf"):
            self.touch(f"Downloads/{n}")
        items = {i["name"]: i["flags"] for i in organizer.scan([self.home / "Downloads"], metadata=False)}
        for n in ("IMG_1234.jpg", "document (3).pdf", "scan0001.pdf", "Untitled.docx"):
            self.assertIn("unclear_name", items[n], n)
        for n in ("Thesis v2.pdf", "CV Mario Rossi.pdf"):
            self.assertNotIn("unclear_name", items[n], n)

    def test_render_with_patterns(self):
        src = self.touch("Downloads/scan0001.pdf")
        cases = {
            "{date} - {title}": "2026-11-03 - Electricity Bill.pdf",
            "{title} - {date}": "Electricity Bill - 2026-11-03.pdf",
            "{year} {category} {title}": "2026 Finance Electricity Bill.pdf",
            "{month}_{title}_{original}": "2026-11_Electricity Bill_scan0001.pdf",
        }
        for pat, want in cases.items():
            with self.subTest(pat=pat):
                out = organizer.render_names([{"src": str(src), "title": "Electricity Bill",
                                               "date": "2026-11-03", "category": "Finance"}], pat)
                self.assertEqual(out[0]["name"], want)

    def test_sanitizing_and_missing_tokens(self):
        src = self.touch("Downloads/IMG_1.jpg")
        out = organizer.render_names([{"src": str(src), "title": 'Trip: Rome/Naples "2026"?'}],
                                     "{category} - {title} - {date}")
        name = out[0]["name"]
        self.assertFalse(any(c in name for c in '<>:"/\\|?*'), name)
        self.assertFalse(name.startswith((" ", "-")), name)
        self.assertRegex(name, r"^Trip Rome Naples 2026 - \d{4}-\d\d-\d\d\.jpg$")
        out = organizer.render_names([{"src": str(src), "title": "CON"}], "{title}")
        self.assertEqual(out[0]["name"], "CON_.jpg")

    def test_names_unique_within_batch_and_on_disk(self):
        a = self.touch("Downloads/IMG_1.jpg")
        b = self.touch("Downloads/IMG_2.jpg")
        self.touch("Downloads/Beach.jpg")
        out = organizer.render_names([{"src": str(a), "title": "Beach"}, {"src": str(b), "title": "Beach"}], "{title}")
        self.assertEqual([o["name"] for o in out], ["Beach (2).jpg", "Beach (3).jpg"])

    def test_uses_config_and_refuses_when_off(self):
        src = self.touch("Downloads/IMG_1.jpg")
        with self.assertRaises(organizer.ConfigError):
            organizer.render_names([{"src": str(src), "title": "X"}])
        self.write_config({"rename_files": {"pattern": "{title} ({year})"}})
        out = organizer.render_names([{"src": str(src), "title": "X", "date": "2024"}])
        self.assertEqual(out[0]["name"], "X (2024).jpg")

    def test_bad_inputs(self):
        src = self.touch("Downloads/IMG_1.jpg")
        (self.home / "Downloads/folder").mkdir()
        out = organizer.render_names([{"src": str(src), "title": ""},
                                      {"src": str(src), "title": "X", "date": "03/11/2026"},
                                      {"src": str(self.home / "Downloads/folder"), "title": "X"}], "{title}")
        self.assertTrue(all("error" in o for o in out), out)
        for bad in ("{date}", "{title}/{date}", "{title} {nope}"):
            with self.subTest(bad=bad):
                with self.assertRaises(organizer.ConfigError):
                    organizer.validate_config({"rename_files": {"pattern": bad}})

    def test_rename_then_undo(self):
        src = self.touch("Downloads/IMG_1.jpg")
        out = organizer.render_names([{"src": str(src), "title": "Beach", "date": "2025-08-01"}], "{date} - {title}")
        run, res = organizer.apply_plan([{"op": "rename", "src": out[0]["src"], "dst": out[0]["dst"]}])
        self.assertEqual(res[0]["status"], "ok")
        self.assertTrue((self.home / "Downloads/2025-08-01 - Beach.jpg").exists())
        organizer.undo(run)
        self.assertTrue(src.exists())

    def test_cli(self):
        src = self.touch("Downloads/IMG_1.jpg")
        f = self.home / "items.json"
        f.write_text(json.dumps([{"src": str(src), "title": "Beach", "date": "2025-08-01"}]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = organizer.main(["render-names", str(f), "--pattern", "{title} {date}"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue())[0]["name"], "Beach 2025-08-01.jpg")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = organizer.main(["report"])
        self.assertEqual(code, 0)
        self.assertIn("biggest", json.loads(buf.getvalue()))
