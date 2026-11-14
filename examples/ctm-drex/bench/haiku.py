"""The frontier baseline: Claude Haiku 5.5 makes the same calls through the Claude Code CLI (`claude -p`).

It gets the same exhibits and the same claims as Drex, all claims of a case in one prompt (Drex gets them
in batches of eight per request), and must answer in JSON. Latency, tokens and cost come from the CLI's
own JSON result. Run with --bare-ish flags so no project instructions or tools inflate its prompt.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from ctm_drex.check import LABELS, VERDICT_OPTIONS, exhibits_state

import os

MODEL = os.environ.get("CTM_BASELINE", "haiku")
PROMPT = """You check an O-1A visa petition draft against its exhibits before filing.
For each numbered claim, decide one verdict:
{options}
and name the exhibit file that is the evidence for or against it, or "none".

{state}

Claims:
{claims}

Reply with JSON only, no prose: [{{"i": 1, "verdict": "supported|contradicted|unsupported", "source": "<file or none>"}}, ...]"""


async def check_claims_haiku(claims: list[str], exhibits: dict[str, str], attempts: int = 3) -> tuple[list[dict], dict]:
    """Retries the whole case until every claim parses; raises if it never does (never cached half-parsed)."""
    last = None
    for _ in range(attempts):
        verdicts, usage = await _once(claims, exhibits)
        if all(v["parsed"] for v in verdicts):
            return verdicts, usage
        last = sum(not v["parsed"] for v in verdicts)
    raise RuntimeError(f"Haiku left {last} claim(s) unparsed after {attempts} attempts")


SYSTEM = "You are a careful legal evidence checker. Answer only in the requested JSON."


def build_prompt(claims: list[str], exhibits: dict[str, str]) -> str:
    """The exact prompt every baseline gets (Claude through the CLI, other models through bench/openai_compat.py)."""
    options = "\n".join(f"- {k}: {v}" for k, v in VERDICT_OPTIONS.items())
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    return PROMPT.format(options=options, state=exhibits_state(exhibits), claims=numbered)


def parse_verdicts(text: str, claims: list[str]) -> list[dict]:
    """One verdict per claim from the model's JSON array; a claim with no valid verdict is 'error', parsed False."""
    rows = []
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
    try:
        rows = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        rows = []
    by_i = {}
    for r in rows:
        try:
            by_i[int(str(r.get("i", 0)).rstrip("."))] = r
        except (ValueError, AttributeError):
            continue
    verdicts = []
    for i, claim in enumerate(claims, 1):
        r = by_i.get(i, {})
        ok = r.get("verdict") in LABELS
        verdicts.append({"claim": claim, "verdict": r["verdict"] if ok else "error", "source": r.get("source"), "parsed": ok})
    return verdicts


async def _once(claims: list[str], exhibits: dict[str, str]) -> tuple[list[dict], dict]:
    prompt = build_prompt(claims, exhibits)
    start = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", MODEL, "--output-format", "json",
        "--tools", "", "--strict-mcp-config", "--setting-sources", "",
        "--system-prompt", SYSTEM,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(prompt.encode())
    seconds = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed: {err.decode()[:300] or out.decode()[:300]}")
    body = json.loads(out)
    if body.get("is_error"):
        raise RuntimeError(f"claude -p returned an error: {str(body.get('result'))[:300]}")
    text = body.get("result", "") or ""
    verdicts = parse_verdicts(text, claims)
    usage = body.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return verdicts, {
        "seconds": seconds,
        "input_tokens": tokens,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cost_usd": float(body.get("total_cost_usd") or 0.0),
        "model": ",".join((body.get("modelUsage") or {}).keys()) or MODEL,
    }
