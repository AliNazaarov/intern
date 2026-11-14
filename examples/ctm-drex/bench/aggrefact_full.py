"""Drex on the FULL LLM-AggreFact test set (Tang, Laban, Durrett 2024), reading each document once.

Claims that share a document go in one request (up to 16 questions), so Drex reads the document once and answers
all of them: read once, ask many. Scores balanced accuracy per sub-dataset at threshold 0.5 (the leaderboard's
metric is per-dataset balanced accuracy, averaged).

    python -m bench.aggrefact_full run <aggrefact_full.jsonl>   # rows: dataset, doc, claim, label
    python -m bench.aggrefact_full score
The data is not committed (CC BY-ND); results store ids, labels and probabilities only.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from bench.aggrefact import INSTRUCTIONS, OPTIONS, balanced_accuracy
from ctm_drex.drex import MAX_QUESTIONS, PRICE_PER_M_INPUT, Choice, Drex

OUT = Path(__file__).resolve().parent / "results"
RAW = OUT / "aggrefact_full_drex.json"
SUMMARY = OUT / "aggrefact_full_summary.json"


async def run(path: str) -> None:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    by_doc: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_doc[r["doc"]].append(i)
    jobs = [(doc, ids[k : k + MAX_QUESTIONS]) for doc, ids in by_doc.items() for k in range(0, len(ids), MAX_QUESTIONS)]
    raw = json.loads(RAW.read_text()) if RAW.exists() else {"rows": {}, "requests": []}
    todo = [j for j in jobs if any(raw["rows"].get(str(i), {}).get("p_supported") is None for i in j[1])]  # new or failed
    print(f"{len(rows)} claims, {len(by_doc)} documents, {len(jobs)} requests, {len(todo)} to do", flush=True)
    done = 0
    start = time.perf_counter()
    async with Drex(concurrency=int(os.environ.get("CTM_DREX_CONCURRENCY", "16"))) as d:
        async def one(doc, ids):
            nonlocal done
            qs = {f"c{n}": Choice(INSTRUCTIONS.format(claim=rows[i]["claim"]), OPTIONS) for n, i in enumerate(ids)}
            try:
                res = await d.ask(doc, qs)
            except Exception as e:
                for i in ids:
                    raw["rows"][str(i)] = {"error": str(e)[:200], "dataset": rows[i]["dataset"], "label": rows[i]["label"]}
                return
            raw["requests"].append({"claims": len(ids), "seconds": res.seconds, "input_tokens": res.input_tokens})
            for n, i in enumerate(ids):
                probs = res.answers.get(f"c{n}", {}).get("probabilities") or {}
                raw["rows"][str(i)] = {"dataset": rows[i]["dataset"], "label": rows[i]["label"],
                                       "p_supported": probs.get("supported")}
            done += 1
            if done % 200 == 0:
                RAW.write_text(json.dumps(raw))
                print(f"{done}/{len(todo)} requests, {time.perf_counter() - start:.0f}s", flush=True)

        await asyncio.gather(*(one(doc, ids) for doc, ids in todo))
    RAW.write_text(json.dumps(raw))
    print("done", flush=True)


def score() -> dict:
    raw = json.loads(RAW.read_text())
    ok = [r for r in raw["rows"].values() if r.get("p_supported") is not None]
    per = defaultdict(list)
    for r in ok:
        per[r["dataset"]].append(r)
    per_ds = {ds: round(balanced_accuracy([r["label"] for r in rs], [int(r["p_supported"] >= 0.5) for r in rs]), 3)
              for ds, rs in sorted(per.items())}
    reqs = raw["requests"]
    tokens = sum(q["input_tokens"] for q in reqs)
    summary = {
        "benchmark": "LLM-AggreFact test set, full (public re-upload NinaCalvi/llm_aggrefact_pre_aug9)",
        "model": "drex-latest (drex-max-v1.1)",
        "claims_scored": len(ok),
        "claims_failed": len(raw["rows"]) - len(ok),
        "requests": len(reqs),
        "threshold": 0.5,
        "per_dataset_balanced_accuracy": per_ds,
        "average_balanced_accuracy": round(statistics.mean(per_ds.values()), 3),
        "input_tokens": tokens,
        "cost_usd": round(tokens * PRICE_PER_M_INPUT / 1e6, 4),
        "seconds_per_request_p50": round(statistics.median(q["seconds"] for q in reqs), 3),
        "claims_per_request_mean": round(statistics.mean(q["claims"] for q in reqs), 2),
    }
    SUMMARY.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    if sys.argv[1] == "run":
        asyncio.run(run(sys.argv[2]))
    else:
        score()
