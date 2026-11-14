"""Run the race on every case, the three lanes started together as on the page, and save bench/results/race.json.

    PYTHONPATH=. ~/.local/share/uv/tools/intern-agent/bin/python race/measure.py [frontier-key]
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

from race import server

OUT = Path(__file__).resolve().parent.parent / "bench" / "results" / "race.json"


async def one(case: str, frontier: str) -> dict:
    lanes = {"drex": server.run_drex(case), "rag": server.run_rag(case), "frontier": server.run_baseline(case, frontier)}
    done = await asyncio.gather(*lanes.values(), return_exceptions=True)
    row = {"case": case}
    for name, res in zip(lanes, done):
        if isinstance(res, Exception):
            row[name] = {"error": f"{type(res).__name__}: {str(res)[:200]}"}
        else:
            row[name] = {k: res[k] for k in ("engine", "seconds", "cost_usd", "correct", "graded", "claims")}
            row[name]["missed_contradictions"] = sum(1 for v in res["verdicts"]
                                                     if v["expected"] == "contradicted" and v["verdict"] != "contradicted")
            row[name]["contradictions"] = sum(1 for v in res["verdicts"] if v["expected"] == "contradicted")
            row[name]["false_supported"] = sum(1 for v in res["verdicts"]
                                               if v["expected"] not in (None, "supported") and v["verdict"] == "supported")
            row[name]["verdicts"] = [v["verdict"] for v in res["verdicts"]]
    print(case, {k: (v.get("seconds"), v.get("correct"), v.get("error")) for k, v in row.items() if k != "case"}, flush=True)
    return row


def summary(rows: list[dict]) -> dict:
    out = {}
    for lane in ("drex", "rag", "frontier"):
        ok = [r[lane] for r in rows if "error" not in r[lane]]
        graded = [x for x in ok if x["graded"]]
        out[lane] = {
            "engine": ok[0]["engine"] if ok else None, "cases": len(ok), "errors": len(rows) - len(ok),
            "median_seconds": statistics.median(x["seconds"] for x in ok) if ok else None,
            "total_cost_usd": sum(x["cost_usd"] for x in ok),
            "correct": sum(x["correct"] for x in graded), "graded": sum(x["graded"] for x in graded),
            "contradictions": sum(x["contradictions"] for x in graded),
            "missed_contradictions": sum(x["missed_contradictions"] for x in graded),
            "false_supported": sum(x["false_supported"] for x in graded),
        }
    return out


async def main(frontier: str) -> None:
    rows = []
    for case in server.CASES:
        rows.append(await one(case, frontier))
    result = {"frontier": frontier, "threshold": server.THRESHOLD, "rag_judge": server.RAG_JUDGE,
              "note": "one run per case, lanes started together; synthetic cases + the Intern demo draft (unlabelled)",
              "summary": summary(rows), "cases": rows}
    OUT.write_text(json.dumps(result, indent=1))
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    server.SPEND_CAP_USD = 3.0
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "or-gpt-5.6-sol"))
