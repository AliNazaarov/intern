"""The API baselines parse answers, tokens and cost like the Claude baselines, with mocked HTTP (no network)."""

import asyncio
import json
import unittest
from unittest import mock

import httpx

from bench import openai_compat as oc


def body(content, usage):
    return {"model": "m", "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": usage}


def run_chat(handler, price=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await oc.chat("sys", "prompt", "m", "https://x.test/v1", "k", {"reasoning_effort": "none"},
                                 client=client, price=price)
    return asyncio.run(go())


class OpenAICompat(unittest.TestCase):
    def test_request_is_temperature_zero_with_extra_fields_and_both_prompts(self):
        seen = {}

        def handler(req):
            seen.update(json.loads(req.content))
            return httpx.Response(200, json=body("{}", {"prompt_tokens": 1, "completion_tokens": 1}))

        run_chat(handler)
        self.assertEqual(seen["temperature"], 0)
        self.assertEqual(seen["reasoning_effort"], "none")
        self.assertEqual([m["role"] for m in seen["messages"]], ["system", "user"])

    def test_provider_cost_wins_over_published_price(self):
        text, u = run_chat(lambda r: httpx.Response(200, json=body("ok", {
            "prompt_tokens": 1000, "completion_tokens": 10, "estimated_cost": 0.5,
            "completion_tokens_details": {"reasoning_tokens": 4}})), price=(1.0, 1.0))
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["reasoning_tokens"]), (1000, 10, 4))
        self.assertEqual(u["cost_usd"], 0.5)
        self.assertIn("estimated_cost", u["price_basis"])

    def test_openrouter_usage_cost_wins_over_published_price(self):
        _, u = run_chat(lambda r: httpx.Response(200, json=body("ok", {
            "prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.0123})), price=(5.0, 30.0))
        self.assertEqual(u["cost_usd"], 0.0123)
        self.assertIn("usage.cost", u["price_basis"])

    def test_openrouter_entries_ask_for_cost_and_set_reasoning(self):
        for name, e in oc.REGISTRY.items():
            if e["provider"] == "openrouter":
                self.assertEqual(e["extra"]["usage"], {"include": True}, name)
                self.assertIn("reasoning", e["extra"], name)

    def test_published_price_is_the_fallback(self):
        _, u = run_chat(lambda r: httpx.Response(200, json=body("ok", {"prompt_tokens": 1_000_000, "completion_tokens": 0})),
                        price=(0.2, 2.5))
        self.assertAlmostEqual(u["cost_usd"], 0.2)
        self.assertIn("published price", u["price_basis"])

    def test_thinking_block_is_stripped_before_parsing(self):
        text, _ = run_chat(lambda r: httpx.Response(200, json=body(
            '<think>maybe [{"i": 1, "verdict": "supported"}]</think>\n```json\n[{"i": 1, "verdict": "contradicted"}]\n```',
            {})))
        self.assertNotIn("think", text)
        from bench.haiku import parse_verdicts
        self.assertEqual(parse_verdicts(text, ["c"])[0]["verdict"], "contradicted")

    def test_retryable_status_is_retried_and_counted_in_seconds(self):
        calls = []

        def handler(req):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=body("ok", {}))

        with mock.patch.object(oc.asyncio, "sleep", new=mock.AsyncMock()):
            text, u = run_chat(handler)
        self.assertEqual((text, len(calls)), ("ok", 2))

    def test_client_error_is_not_retried(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(401, json={"error": "bad key"})

        with self.assertRaises(RuntimeError):
            run_chat(handler)
        self.assertEqual(len(calls), 1)

    def test_case_reasks_until_parsed_and_sums_spend(self):
        answers = iter(["not json", '[{"i": 1, "verdict": "supported", "source": "E1.md"}, {"i": 2, "verdict": "unsupported"}]'])
        usage = {"seconds": 1.0, "input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "cost_usd": 0.01,
                 "model": "m", "price_basis": "p", "finish_reason": "stop"}

        async def fake_chat(*a, **k):
            return next(answers), dict(usage)

        with mock.patch.object(oc, "chat", new=fake_chat):
            verdicts, u = asyncio.run(oc.check_claims_openai(["a", "b"], {"E1.md": "x"}, "m", "u", "k"))
        self.assertEqual([v["verdict"] for v in verdicts], ["supported", "unsupported"])
        self.assertTrue(all(v["parsed"] for v in verdicts))
        self.assertEqual((u["attempts"], u["input_tokens"]), (2, 20))
        self.assertAlmostEqual(u["cost_usd"], 0.02)

    def test_case_never_parsed_is_marked_not_raised(self):
        async def fake_chat(*a, **k):
            return "no", {"seconds": 1.0, "input_tokens": 1, "output_tokens": 1, "reasoning_tokens": 0, "cost_usd": 0.0}

        with mock.patch.object(oc, "chat", new=fake_chat):
            verdicts, u = asyncio.run(oc.check_claims_openai(["a"], {}, "m", "u", "k"))
        self.assertEqual((verdicts[0]["verdict"], verdicts[0]["parsed"], u["attempts"]), ("error", False, 3))

    def test_aggrefact_record_uses_haiku_parser(self):
        async def fake_chat(*a, **k):
            return '```json\n{"verdict": "unsupported"}\n```', {"seconds": 0.5, "input_tokens": 5, "output_tokens": 3,
                                                               "reasoning_tokens": 0, "cost_usd": 0.001}

        with mock.patch.object(oc, "chat", new=fake_chat):
            rec = asyncio.run(oc.aggrefact_one_openai({"doc": "d", "claim": "c"}, "m", "u", "k"))
        self.assertEqual((rec["verdict"], rec["input_tokens"], rec["cost_usd"]), ("unsupported", 5, 0.001))

    def test_registry_names_are_file_safe_and_providers_known(self):
        for name, e in oc.REGISTRY.items():
            self.assertRegex(name, r"^[a-z0-9.\-]+$")
            self.assertIn(e["provider"], oc.PROVIDERS)
            self.assertTrue(e["thinking"])


if __name__ == "__main__":
    unittest.main()
