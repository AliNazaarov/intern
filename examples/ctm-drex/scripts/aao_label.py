"""Label scraped AAO decisions: form, outcome, each evidence criterion (met / not met / not addressed), main reason.

A model reads each decision once (Gemma 4 31B on DeepInfra by default; Drex once the Nace console answers).
Code cross-checks the outcome against the decision's own "ORDER:" line, so the label quality is measured, not assumed.
  uv run --with httpx python scripts/aao_label.py label data/aao [MODEL]     # -> data/aao/labels.jsonl (resumable)
  uv run --with httpx python scripts/aao_label.py stats data/aao data/aao_19  # -> bench/results/aao_stats.json
"""

from __future__ import annotations

import asyncio
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.openai_compat import chat, resolve  # noqa: E402

BUDGET_USD = 2.0
MAX_CHARS = 60_000  # longer decisions are cut; the conclusion and ORDER are kept from the end
CRITERIA = {
    "awards": "nationally or internationally recognized prizes or awards",
    "membership": "membership in associations requiring outstanding achievement",
    "press": "published material about the person in major media or trade publications",
    "judging": "judging the work of others",
    "contributions": "original contributions of major significance",
    "articles": "authorship of scholarly articles",
    "exhibitions": "display of work at artistic exhibitions (EB-1A only)",
    "critical_role": "leading or critical role for distinguished organizations",
    "salary": "high salary or remuneration",
    "commercial": "commercial success in the performing arts (EB-1A only)",
}
REASONS = {
    "claims_not_backed": "the evidence filed does not show what the petition claims",
    "missing_documents": "required documents or consultation were not submitted",
    "not_credible": "evidence was not independent, objective or credible",
    "final_merits": "enough criteria met but failed the final merits (sustained acclaim, top of field)",
    "procedural": "procedural: late, wrong form, moot, motion requirements not met",
    "other": "other",
}
PROMPT = """You label a USCIS Administrative Appeals Office (AAO) decision. Read it and answer in JSON only:
{{"form": "O-1A|O-1B|O-2|EB-1A|other",
 "outcome": "dismissed|sustained|remanded|rejected|withdrawn|other",
 "criteria": {{{crit}}},
 "main_reason": "{reasons}"}}
For each criterion: "met" if the AAO (or the director, undisputed) found it met, "not_met" if found not met,
"not_addressed" if the decision does not decide it. Criteria:
{crit_help}
main_reason (why the petition failed; "other" if it was approved):
{reason_help}

DECISION:
{text}"""


def build_prompt(text: str) -> str:
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS // 2] + "\n[...]\n" + text[-MAX_CHARS // 2 :]
    return PROMPT.format(
        crit=", ".join(f'"{k}": "met|not_met|not_addressed"' for k in CRITERIA),
        reasons="|".join(REASONS),
        crit_help="\n".join(f"- {k}: {v}" for k, v in CRITERIA.items()),
        reason_help="\n".join(f"- {k}: {v}" for k, v in REASONS.items()),
        text=text,
    )


_ORDER = re.compile(r"\bORDER\s*:\s*(.{0,160})", re.S)  # capitals and colon: "in order to" is not an order


def order_outcome(text: str) -> str | None:
    """The outcome as the decision's own ORDER line states it (last ORDER wins), or None if there is none."""
    hits = _ORDER.findall(text)
    if not hits:
        return None
    s = " ".join(hits[-1].split()).lower()
    for word in ("sustained", "remanded", "rejected", "withdrawn", "dismissed", "denied"):
        if word in s:
            return "dismissed" if word == "denied" else word
    return "other"


def parse(answer: str) -> dict | None:
    m = re.search(r"\{.*\}", answer, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("criteria"), dict):
        return None
    return d


async def label(folder: Path, model_name: str) -> None:
    import httpx

    m = resolve(model_name)
    out = folder / "labels.jsonl"
    done = {json.loads(l)["id"] for l in out.read_text().splitlines()} if out.exists() else set()
    todo = [p for p in sorted((folder / "text").glob("*.txt")) if p.stem not in done and p.stat().st_size > 500]
    spent = 0.0
    sem = asyncio.Semaphore(16)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15)) as client:

        async def one(p: Path) -> None:
            nonlocal spent
            if spent >= BUDGET_USD:
                return
            text = p.read_text()
            async with sem:
                try:
                    answer, usage = await chat("Answer only in the requested JSON.", build_prompt(text), m["model"],
                                               m["base_url"], m["key"], m.get("extra"), client=client, price=m["price"])
                except Exception as e:  # one failed decision is skipped, not fatal; rerun resumes it
                    print(f"failed {p.stem}: {str(e)[:120]}", file=sys.stderr)
                    return
            row = {"id": p.stem, "model": usage["model"], "order_outcome": order_outcome(text),
                   "label": parse(answer), "cost_usd": usage["cost_usd"], "input_tokens": usage["input_tokens"]}
            async with lock:
                spent += usage["cost_usd"]
                with out.open("a") as f:
                    f.write(json.dumps(row) + "\n")

        await asyncio.gather(*(one(p) for p in todo))
    print(f"labelled {len(todo)} in {folder} (${spent:.3f} this run; budget ${BUDGET_USD})")


def stats(folders: list[Path]) -> dict:
    report = {}
    for folder in folders:
        rows = [json.loads(l) for l in (folder / "labels.jsonl").read_text().splitlines()]
        for r in rows:  # recomputed from the text, so a fix to order_outcome applies to rows already labelled
            r["order_outcome"] = order_outcome((folder / "text" / f"{r['id']}.txt").read_text())
        ok = [r for r in rows if r["label"]]
        agree = [r for r in ok if r["order_outcome"] in ("dismissed", "sustained", "remanded", "rejected", "withdrawn")]
        crit = {k: collections.Counter() for k in CRITERIA}
        for r in ok:
            for k, v in r["label"]["criteria"].items():
                if k in crit:
                    crit[k][v] += 1
        report[folder.name] = {
            "decisions": len(rows),
            "parsed": len(ok),
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "model": rows[0]["model"] if rows else None,
            "outcome_by_order_line": collections.Counter(r["order_outcome"] or "none" for r in rows),
            "outcome_by_model": collections.Counter(r["label"].get("outcome") for r in ok),
            "model_agrees_with_order_line": f"{sum(r['label'].get('outcome') == r['order_outcome'] for r in agree)}/{len(agree)}",
            "form": collections.Counter(r["label"].get("form") for r in ok),
            "main_reason": collections.Counter(r["label"].get("main_reason") for r in ok),
            "criteria": {k: dict(c) for k, c in crit.items()},
        }
    return report


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "label":
        asyncio.run(label(Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "di-gemma-4-31b"))
    elif cmd == "stats":
        rep = stats([Path(a) for a in sys.argv[2:]])
        dest = ROOT / "bench" / "results" / "aao_stats.json"
        dest.write_text(json.dumps(rep, indent=2))
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
