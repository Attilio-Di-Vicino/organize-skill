import io
import json
from contextlib import redirect_stdout

from base import TmpHome, organizer


class OnboardingTest(TmpHome):
    def cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = organizer.main(list(argv))
        return code, json.loads(buf.getvalue())

    def apply(self, answers, *extra):
        f = self.home / "answers.json"
        f.write_text(json.dumps(answers))
        return self.cli("onboarding", "--apply", str(f), *extra)

    def test_questions_file_is_well_formed(self):
        ob = organizer.load_onboarding()
        ids = set()
        for r in ob["rounds"]:
            self.assertLessEqual(len(r["questions"]), 4, r["id"])  # AskUserQuestion limit
            for q in r["questions"]:
                self.assertNotIn(q["id"], ids)
                ids.add(q["id"])
                self.assertLessEqual(len(q["header"]), 12, q["id"])
                self.assertTrue(2 <= len(q["options"]) <= 4, q["id"])
                for qid in (q.get("when") or {}):
                    self.assertIn(qid, ids, f"{q['id']} depends on a later question")

    def test_every_option_produces_a_valid_config(self):
        ob = organizer.load_onboarding()
        for r in ob["rounds"]:
            for q in r["questions"]:
                for o in q["options"]:
                    answers = {k: v for k, v in (q.get("when") or {}).items()}
                    answers = {k: (v[0] if isinstance(v, list) else v) for k, v in answers.items()}
                    if o.get("values_to"):
                        answers[q["id"]] = {"option": o["id"], "values": ["Alpha", "Beta"]}
                    elif o.get("text_to"):
                        answers[q["id"]] = {"option": o["id"], "text": "{year} {title}"}
                    else:
                        answers[q["id"]] = o["id"]
                    if q["id"] == "documents" and o["id"] == "skip":
                        answers.update(desktop="sections", downloads="junk_only")
                    with self.subTest(q=q["id"], o=o["id"]):
                        organizer.onboarding_to_config(answers)

    def test_needed_until_done(self):
        _, info = self.cli("info")
        self.assertTrue(info["onboarding_needed"])
        code, out = self.apply({"desktop": "sections",
                                "desktop_sections": {"option": "custom", "values": ["Lavoro", "Da fare"]},
                                "downloads": "empty", "documents": "topics", "trash": "never",
                                "language": "mine", "_user_language": "it"})
        self.assertEqual(code, 0, out)
        cfg = out["config"]
        self.assertEqual(cfg["folders"]["desktop"], {"policy": "tidy", "sections": ["Lavoro", "Da fare"]})
        self.assertEqual(cfg["trash"], "never")
        self.assertEqual(cfg["naming"]["language"], "it")
        _, info = self.cli("info")
        self.assertFalse(info["onboarding_needed"])

    def test_dry_run_does_not_save(self):
        code, out = self.apply({"desktop": "skip"}, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIsNone(out["written"])
        self.assertFalse((self.home / ".organize/config.json").exists())

    def test_conditional_question_ignored_when_not_applicable(self):
        _, out = self.apply({"desktop": "empty",
                             "desktop_sections": {"option": "custom", "values": ["X"]}})
        self.assertEqual(out["config"]["folders"]["desktop"], {"policy": "empty", "sections": []})

    def test_rerun_keeps_unrelated_settings_and_drops_stale_sections(self):
        (self.home / ".organize").mkdir()
        (self.home / ".organize/config.json").write_text(json.dumps(
            {"keep": ["*.lnk"], "folders": {"desktop": {"policy": "tidy", "sections": ["A"]}}}))
        _, out = self.apply({"desktop": "junk_only"})
        self.assertEqual(out["config"]["keep"], ["*.lnk"])
        self.assertEqual(out["config"]["folders"]["desktop"], {"policy": "keep", "sections": []})
        self.assertTrue((self.home / ".organize/config.json.bak").exists())

    def test_conflict_reported(self):
        code, out = self.apply({"desktop": "empty", "documents": "skip"})
        self.assertEqual(code, 1)
        self.assertIn("destination", out["detail"])
        self.assertFalse((self.home / ".organize/config.json").exists())

    def test_bad_answers(self):
        for bad in ({"desktop": "explode"}, {"nope": "x"},
                    {"desktop": "sections", "desktop_sections": "custom"}):
            with self.subTest(bad=bad):
                code, out = self.apply(bad)
                self.assertEqual(code, 1)

    def test_broken_config_can_be_replaced_by_onboarding(self):
        (self.home / ".organize").mkdir()
        (self.home / ".organize/config.json").write_text("{oops")
        code, out = self.apply({"desktop": "skip"})
        self.assertEqual(code, 0, out)
        code, _ = self.cli("config")
        self.assertEqual(code, 0)

    # first time is mandatory, later only on request

    def test_manual_config_without_onboarding_still_needs_it(self):
        (self.home / ".organize").mkdir()
        (self.home / ".organize/config.json").write_text(json.dumps({"trash": "never"}))
        _, info = self.cli("info")
        self.assertTrue(info["onboarding_needed"])

    def test_new_questions_are_announced_not_forced(self):
        (self.home / ".organize").mkdir()
        (self.home / ".organize/config.json").write_text(json.dumps({"onboarding_version": 1}))
        _, info = self.cli("info")
        self.assertFalse(info["onboarding_needed"])
        self.assertIn("rename", info["onboarding_new_questions"])
        self.assertNotIn("desktop", info["onboarding_new_questions"])

    def test_current_answers_reflect_config(self):
        _, out = self.apply({"desktop": "sections",
                             "desktop_sections": {"option": "custom", "values": ["A"]},
                             "trash": "never", "rename": "title_first", "stale": "2y",
                             "language": "mine", "_user_language": "it"})
        _, ob = self.cli("onboarding")
        cur = ob["current_answers"]
        self.assertEqual(cur["desktop"], "sections")
        self.assertEqual(cur["desktop_sections"], "custom")
        self.assertEqual(cur["trash"], "never")
        self.assertEqual(cur["rename"], "title_first")
        self.assertEqual(cur["stale"], "2y")
        self.assertEqual(cur["language"], "mine")
        self.assertEqual(cur["protected"], "no")

    def test_partial_rerun_uses_config_for_conditions(self):
        self.apply({"desktop": "sections"})
        # only the follow-up is re-asked: its `when` is satisfied by the saved config
        _, out = self.apply({"desktop_sections": {"option": "custom", "values": ["Inbox", "Done"]}})
        self.assertEqual(out["config"]["folders"]["desktop"]["sections"], ["Inbox", "Done"])
        self.assertEqual(out["config"]["folders"]["desktop"]["policy"], "tidy")

    def test_custom_rename_pattern(self):
        code, out = self.apply({"rename": {"option": "custom", "text": "{year} {category} {title}"},
                                "rename_scope": "all"})
        self.assertEqual(code, 0, out)
        self.assertEqual(out["config"]["rename_files"], {"pattern": "{year} {category} {title}", "scope": "all"})
        code, out = self.apply({"rename": {"option": "custom", "text": "{when} {title}"}})
        self.assertEqual(code, 1)
