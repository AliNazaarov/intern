import asyncio
import unittest

from ctm_drex.check import check_claims, decide, extract_claims, numbers_missing, receipt
from ctm_drex.drex import Result


class FakeDrex:
    """Answers every verdict question with fixed probabilities and every source question with E1."""

    model = "fake"

    def __init__(self, probs):
        self.probs = probs
        self.asks = 0

    async def ask(self, state, questions):
        self.asks += 1
        answers = {}
        for name in questions:
            if name.startswith("v"):
                answers[name] = {"choice": max(self.probs, key=self.probs.get), "probabilities": self.probs}
            else:
                answers[name] = {"choice": "E1.md"}
        return Result(answers=answers, input_tokens=100, seconds=0.1, model="fake")


EXHIBITS = {"E1.md": "Offer letter. Title: Senior Engineer. Team of 14. Salary $182,000."}


class CheckTests(unittest.TestCase):
    def test_extract_skips_headings_and_short_lines(self):
        draft = "# Critical Role\n\nShe led a team of 14 engineers at Acme. Thanks.\n- Ok\nHer salary was $182,000 in 2025."
        self.assertEqual(
            extract_claims(draft),
            ["She led a team of 14 engineers at Acme.", "Her salary was $182,000 in 2025."],
        )

    def test_numbers_missing_normalises_units(self):
        self.assertEqual(numbers_missing("paid $182k", EXHIBITS["E1.md"]), set())
        self.assertEqual(numbers_missing("a team of 40", EXHIBITS["E1.md"]), {40.0})

    def test_decide_threshold(self):
        p = {"supported": 0.6, "contradicted": 0.3, "unsupported": 0.1}
        self.assertEqual(decide(p, 0.5), "supported")
        self.assertEqual(decide(p, 0.7), "contradicted")

    def test_number_rule_overrides_supported(self):
        drex = FakeDrex({"supported": 0.9, "contradicted": 0.07, "unsupported": 0.03})
        verdicts, _ = asyncio.run(check_claims(["She led a team of 40 engineers."], EXHIBITS, drex))
        self.assertEqual(verdicts[0].verdict, "contradicted")
        self.assertIn("40", verdicts[0].note)

    def test_batches_eight_claims_per_request(self):
        drex = FakeDrex({"supported": 0.9, "contradicted": 0.05, "unsupported": 0.05})
        claims = [f"She is a Senior Engineer, claim {i}." for i in range(17)]
        verdicts, usage = asyncio.run(check_claims(claims, EXHIBITS, drex, number_rule=False))
        self.assertEqual((len(verdicts), drex.asks, usage["requests"]), (17, 3, 3))

    def test_receipt_counts_flagged(self):
        drex = FakeDrex({"supported": 0.1, "contradicted": 0.2, "unsupported": 0.7})
        verdicts, usage = asyncio.run(check_claims(["She founded a robotics lab at MIT."], EXHIBITS, drex))
        text = receipt(verdicts, usage)
        self.assertIn("CLIENT-STATED ONLY", text)
        self.assertIn("1 claim(s) need evidence", text)


if __name__ == "__main__":
    unittest.main()
