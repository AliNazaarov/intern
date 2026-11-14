"""Baselines served by OpenAI-compatible chat APIs (DeepInfra, OpenRouter, OpenAI, Gemini).

They get exactly the prompts the Claude baselines get: bench/haiku.py's PROMPT and system prompt for the synthetic
petitions, bench/aggrefact.py's HAIKU_PROMPT and HAIKU_SYSTEM for AggreFact, parsed by the same functions.
Select one with CTM_BASELINE=<name from REGISTRY>; bench/run.py and bench/aggrefact.py route to this module.

Per call: seconds is the wall clock of the HTTP call, retries and backoff included; tokens come from `usage`;
cost_usd is the provider's own cost when it returns one (DeepInfra `usage.estimated_cost`, OpenRouter `usage.cost`), else tokens times the published price
in REGISTRY, and `price_basis` says which. temperature 0. Thinking is switched off where the model allows it
(see each entry's `thinking` note). API keys come from the environment or ~/.intern/.env and are never printed.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

import httpx

PROVIDERS = {
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}

# name (used as CTM_BASELINE and in result file names) -> provider, model id, extra request fields, published
# price in USD per million input/output tokens (fallback only), and what was done about thinking.
# DeepInfra prices are from GET /v1/openai/models (metadata.pricing) on 2026-11-14.
REGISTRY: dict[str, dict] = {
    "di-qwen3.8-27b": {
        "provider": "deepinfra", "model": "Qwen/Qwen3.8-27B", "price": (0.20, 2.50),
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "thinking": "off: chat_template_kwargs.enable_thinking=false (it thinks by default: 59 vs 8 output tokens on a probe)",
    },
    "di-qwen3.8-max": {
        "provider": "deepinfra", "model": "Qwen/Qwen3.8-Max", "price": (1.65, 4.951),
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "thinking": "off: chat_template_kwargs.enable_thinking=false (it thinks by default: 28 reasoning tokens on a probe)",
    },
    "di-kimi-k3": {
        "provider": "deepinfra", "model": "moonshotai/Kimi-K3", "price": (2.85, 14.25),
        "extra": {"chat_template_kwargs": {"thinking": False}},
        "thinking": "off: chat_template_kwargs.thinking=false (it thinks by default: 70 reasoning tokens on a probe)",
    },
    "di-deepseek-v4-pro": {
        "provider": "deepinfra", "model": "deepseek-ai/DeepSeek-V4-Pro", "price": (1.30, 2.60),
        "extra": {"chat_template_kwargs": {"thinking": False}},
        "thinking": "off: chat_template_kwargs.thinking=false (no thinking seen by default either)",
    },
    "di-llama-3.3-70b": {
        "provider": "deepinfra", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "price": (0.10, 0.32),
        "extra": {}, "thinking": "n/a: no thinking mode",
    },
    "di-gemma-4-31b": {
        "provider": "deepinfra", "model": "google/gemma-4-31B-it", "price": (0.20, 0.40),
        "extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "thinking": "off: chat_template_kwargs.enable_thinking=false (no thinking seen by default either)",
    },
    "di-gemini-3.1-pro": {
        "provider": "deepinfra", "model": "google/gemini-3.1-pro", "price": (2.00, 12.00),
        "extra": {"reasoning_effort": "none"},
        "thinking": "off: reasoning_effort=none (probe: 0 reasoning tokens; default spends ~180 on a one-line question)",
    },
    "di-gpt-oss-120b": {
        "provider": "deepinfra", "model": "openai/gpt-oss-120b", "price": (0.037, 0.17),
        "extra": {"reasoning_effort": "low"},
        "thinking": "on at its minimum: reasoning_effort=low (gpt-oss always reasons; none gave the same output)",
    },
    # OpenRouter prices are from GET /api/v1/models (pricing) on 2026-11-14; usage.include=true returns usage.cost.
    # gpt-5.6 comes in sol / terra / luna tiers (each also as -pro); sol is the strongest non-pro tier.
    # OpenRouter does not list temperature for the OpenAI models, so temperature 0 may be dropped for them.
    "or-gpt-5.6-sol": {
        "provider": "openrouter", "model": "openai/gpt-5.6-sol", "price": (4.00, 20.00),
        "extra": {"reasoning": {"enabled": False}, "usage": {"include": True}},
        "thinking": "off: reasoning.enabled=false (probe: 0 reasoning tokens; also 0 by default)",
    },
    "or-gpt-5.5": {
        "provider": "openrouter", "model": "openai/gpt-5.5", "price": (5.00, 30.00),
        "extra": {"reasoning": {"enabled": False}, "usage": {"include": True}},
        "thinking": "off: reasoning.enabled=false (probe: 0 reasoning tokens; default spends 26)",
    },
    "or-grok-4.7": {
        "provider": "openrouter", "model": "x-ai/grok-4.7", "price": (2.00, 6.00),
        "extra": {"reasoning": {"effort": "minimal"}, "usage": {"include": True}},
        "thinking": "on at its minimum: reasoning.effort=minimal (cannot be disabled: HTTP 400; probe 82 vs 96 reasoning tokens by default)",
    },
    "or-gemini-3.8-flash": {
        "provider": "openrouter", "model": "google/gemini-3.8-flash", "price": (0.825, 4.125),
        "extra": {"reasoning": {"effort": "minimal"}, "usage": {"include": True}},
        "thinking": "on at its minimum: reasoning.effort=minimal (cannot be disabled: HTTP 400; probe 163 vs 157 reasoning tokens by default)",
    },
}

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}
THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def key_for(env_name: str) -> str:
    """The API key from the environment or ~/.intern/.env (same parsing as ctm_drex/drex.py). Never printed."""
    key = os.environ.get(env_name)
    if not key:
        env = Path.home() / ".intern" / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                name, _, value = line.partition("=")
                if name.strip().removeprefix("export ").strip() == env_name and value.strip():
                    key = value.strip().strip("'\"")
    if not key:
        raise RuntimeError(f"{env_name} is not set (environment or ~/.intern/.env)")
    return key


def resolve(name: str) -> dict:
    """Registry entry plus base_url and key, ready to pass to the check functions."""
    entry = dict(REGISTRY[name])
    base_url, env_name = PROVIDERS[entry["provider"]]
    entry.update(base_url=base_url, key=key_for(env_name))
    return entry


def strip_thinking(text: str) -> str:
    """Drop <think>...</think> blocks some models put in the answer, so a JSON-looking scratchpad is not parsed."""
    return THINK.sub("", text or "").strip()


def usage_of(body: dict, price: tuple[float, float] | None) -> dict:
    """Tokens and cost from a chat-completions body. Provider cost wins; the published price is the fallback."""
    u = body.get("usage") or {}
    tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    details = u.get("completion_tokens_details") or {}
    if u.get("estimated_cost") is not None:
        cost, basis = float(u["estimated_cost"]), "provider usage.estimated_cost"
    elif u.get("cost") is not None:  # OpenRouter, with usage.include=true: what the account was charged
        cost, basis = float(u["cost"]), "provider usage.cost (OpenRouter)"
    elif price:
        cost = (tin * price[0] + tout * price[1]) / 1e6
        basis = f"published price ${price[0]}/M in, ${price[1]}/M out"
    else:
        cost, basis = 0.0, "unknown: no provider cost and no published price"
    return {"input_tokens": tin, "output_tokens": tout, "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
            "cost_usd": cost, "price_basis": basis}


async def chat(system: str, prompt: str, model: str, base_url: str, key: str, extra: dict | None = None,
               client: httpx.AsyncClient | None = None, price: tuple[float, float] | None = None,
               tries: int = 4) -> tuple[str, dict]:
    """One chat completion at temperature 0. Returns (answer text, usage incl. seconds with retries)."""
    payload = {"model": model, "temperature": 0, "max_tokens": 4096,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
               **(extra or {})}
    headers = {"Authorization": f"Bearer {key}"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0))
    start = time.perf_counter()
    last: Exception | None = None
    try:
        for attempt in range(tries):
            try:
                r = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                if r.status_code in RETRYABLE:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                if r.status_code >= 400:  # bad request, auth, unknown model: retrying will not help
                    raise RuntimeError(f"{model} refused the request: HTTP {r.status_code} {r.text[:200]}")
                body = r.json()
                msg = (body.get("choices") or [{}])[0].get("message") or {}
                usage = usage_of(body, price)
                usage.update(seconds=time.perf_counter() - start, model=body.get("model") or model,
                             finish_reason=(body.get("choices") or [{}])[0].get("finish_reason"))
                return strip_thinking(msg.get("content") or ""), usage
            except (httpx.HTTPError, ValueError) as e:
                last = e
                if attempt < tries - 1:
                    await asyncio.sleep(2 * 2 ** attempt)
        raise RuntimeError(f"{model}: no answer after {tries} tries: {str(last)[:200]}")
    finally:
        if own:
            await client.aclose()


async def check_claims_openai(claims: list[str], exhibits: dict[str, str], model: str, base_url: str, key: str,
                              extra: dict | None = None, price: tuple[float, float] | None = None,
                              attempts: int = 3) -> tuple[list[dict], dict]:
    """Same prompt and parsing as check_claims_haiku. Re-asks the whole case until every claim parses (like Haiku),
    but returns the last answer with unparsed claims marked instead of raising, so they are counted, not hidden.
    Seconds, tokens and cost are summed over every attempt: what the run actually waited and paid."""
    from bench.haiku import SYSTEM, build_prompt, parse_verdicts

    prompt = build_prompt(claims, exhibits)
    total = {"seconds": 0.0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0}
    verdicts: list[dict] = []
    usage: dict = {}
    for n in range(1, attempts + 1):
        text, usage = await chat(SYSTEM, prompt, model, base_url, key, extra, price=price)
        for k in total:
            total[k] += usage[k]
        verdicts = parse_verdicts(text, claims)
        if all(v["parsed"] for v in verdicts):
            break
    return verdicts, {**total, "attempts": n, "model": usage.get("model", model),
                      "price_basis": usage.get("price_basis"), "finish_reason": usage.get("finish_reason")}


async def aggrefact_one_openai(r: dict, model: str, base_url: str, key: str, extra: dict | None = None,
                               price: tuple[float, float] | None = None,
                               client: httpx.AsyncClient | None = None) -> dict:
    """One AggreFact claim with Haiku's prompt and parser; the record has the same fields as haiku_one's."""
    from bench.aggrefact import HAIKU_PROMPT, HAIKU_SYSTEM, parse_verdict

    try:
        text, usage = await chat(HAIKU_SYSTEM, HAIKU_PROMPT.format(doc=r["doc"], claim=r["claim"]),
                                 model, base_url, key, extra, client=client, price=price)
    except Exception as e:  # recorded as an error row, retried on the next run, never scored
        return {"error": str(e)[:300]}
    return {"verdict": parse_verdict(text), "text": text[:500], **usage}
