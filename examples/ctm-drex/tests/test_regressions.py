"""Regression tests for the independent review's findings, one per fix."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ctm_drex import check
from ctm_drex.check import Verdict, extract_claims, numbers_missing, receipt
from ctm_drex.drex import Result


class FakeDrex:
    model = "fake"

    def __init__(self, answers):
        self.answers = answers

    async def ask(self, state, questions):
        return Result(answers=self.answers, input_tokens=10, seconds=0.1, model="fake")


class Regressions(unittest.TestCase):
    def test_unit_words_need_a_boundary(self):  # review #1: "14 members" is 14, not 14 million
        self.assertEqual(numbers_missing("a team of 14 members", "14 people"), set())

    def test_labels_are_not_numbers(self):  # review #1: E3 and O-1A are labels
        self.assertEqual(numbers_missing("filed an O-1A under E3", "nothing"), set())

    def test_file_names_are_not_numbers(self):  # demo: "(E4-payroll-2025.md)" read as 25
        self.assertEqual(numbers_missing("paid $151,666.70 in 2025 (E4-payroll-2025.md)", "$151,666.70 in 2025"), set())

    def test_legal_citations_are_not_numbers(self):  # demo: "8 C.F.R. § 214.2(o)" read as 214.2
        self.assertEqual(numbers_missing("satisfies 8 C.F.R. § 214.2(o)(3)(iii)(B)(7)", "no numbers"), set())

    def test_number_rule_reads_every_cited_exhibit(self):  # demo run2 claim 34
        ex = {"E1.md": "salary $182,000", "E4.md": "paid", "E5.md": "a $250k package"}
        text = check._cited_text("$182,000 (E1.md; E4.md) and $250k (E5.md)", "E4.md", ex)
        self.assertEqual(numbers_missing("$182,000 and $250k", text), set())

    def test_marked_claims_are_not_counted_as_needing_rewrite(self):
        vs = [Verdict("Her role is critical [client to provide evidence].", "unsupported", {"unsupported": 1.0}, None),
              Verdict("She leads dozens.", "contradicted", {"contradicted": 1.0}, None)]
        self.assertIn("1 claim(s) need evidence or a rewrite before filing; 1 already marked", receipt(vs, {"model": "m", "seconds": 0.1}))

    def test_hyphenated_year_range_keeps_the_end_year(self):  # review 2
        self.assertEqual(numbers_missing("worked 2019-2022", "worked 2019-2021"), {2022.0})
        self.assertEqual(numbers_missing("an O-1A and E-3 case", "nothing"), set())

    def test_marker_never_excuses_a_contradiction(self):  # review 1
        v = Verdict("She leads dozens [client to provide evidence].", "contradicted", {"contradicted": 1.0}, None)
        self.assertFalse(check.awaiting_client(v))

    def test_billion(self):
        self.assertEqual(numbers_missing("raised $1.2 billion", "raised $1,200,000,000"), set())

    def test_abbreviations_do_not_split_claims(self):  # review #9
        claims = extract_claims("Dr. Mira Lund led the robotics team at Kestrelbay Inc. for four years.")
        self.assertEqual(len(claims), 1)

    def test_missing_drex_answer_is_an_error_not_a_verdict(self):  # review #3
        verdicts, _ = asyncio.run(check.check_claims(["Mira led a team of 6 engineers."], {"a.md": "x"}, FakeDrex({})))
        self.assertEqual(verdicts[0].verdict, "error")
        self.assertIn("NOT CHECKED", receipt(verdicts, {"model": "fake", "seconds": 0.1}))


class InternGate(unittest.TestCase):  # review #8
    def setUp(self):
        try:
            from ctm_drex.intern_ext import CtmReceipts
        except ImportError:
            self.skipTest("intern not installed")
        self.gate = CtmReceipts()
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "exhibits").mkdir()
        (self.dir / "exhibits" / "a.md").write_text("Mira led a pod of 6 engineers.")
        (self.dir / "petition-draft.md").write_text("Mira led a team of dozens of engineers at Kestrelbay.")
        self.ctx = SimpleNamespace(cwd=str(self.dir))

    def test_failure_writes_check_failed_receipt(self):
        with mock.patch("ctm_drex.intern_ext.check_claims", side_effect=RuntimeError("Drex down")):
            out = asyncio.run(self.gate.agent_end(SimpleNamespace(reason="completed"), self.ctx))
        self.assertIsNone(out)
        self.assertTrue((self.dir / "ctm-receipt.md").read_text().startswith("CHECK FAILED"))

    def test_no_draft_writes_not_checked(self):  # review 3: a stale receipt must not look current
        (self.dir / "ctm-receipt.md").write_text("Round 1 old receipt")
        (self.dir / "petition-draft.md").unlink()
        asyncio.run(self.gate.agent_end(SimpleNamespace(reason="completed"), self.ctx))
        self.assertTrue((self.dir / "ctm-receipt.md").read_text().startswith("NOT CHECKED"))

    def test_stopped_run_is_not_checked(self):
        with mock.patch("ctm_drex.intern_ext.check_claims") as cc:
            asyncio.run(self.gate.agent_end(SimpleNamespace(reason="aborted"), self.ctx))
        cc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
