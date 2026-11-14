"""Benchmark: Drex against Claude Haiku 5.5 on labelled synthetic O-1A claims.

    python -m bench.run drex      # call Drex on every case, cache raw answers
    python -m bench.run haiku     # call Haiku on every case, cache answers
    python -m bench.run telepathy # one state, many questions vs one question per request
    python -m bench.run score     # fit the threshold on dev, score everything on test, write results.md

Raw model answers are cached in bench/results/ so scoring never re-calls a model.
"""

from __future__ import annotations

import asyncio
import os
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from ctm_drex.check import (
    DEFAULT_THRESHOLD, LABELS, _cited_text, Verdict, check_claims, decide, exhibits_state, load_exhibits, numbers_missing, receipt,
)
from ctm_drex.drex import PRICE_PER_M_INPUT, Choice, Drex

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "bench" / "results"
BL = os.environ.get("CTM_BASELINE", "haiku")  # frontier baseline: haiku or opus
CASES = os.environ.get("CTM_CASES", "cases")  # cases_pdf: exhibits as PDFs parsed by NDI
TAG = "" if CASES == "cases" else "_" + CASES


def load_cases() -> dict[str, list[dict]]:
    cases: dict[str, list[dict]] = defaultdict(list)
    for line in (DATA / "labels.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            cases[row["case"]].append(row)
    return dict(sorted(cases.items()))


def exhibits_of(case: str) -> dict[str, str]:
    return load_exhibits(DATA / CASES / case / "exhibits")


async def run_drex() -> None:
    cases = load_cases()
    path = OUT / f"drex_raw{TAG}.json"
    out = json.loads(path.read_text()) if path.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    async with Drex() as drex:
        # One case at a time, so each petition's seconds is what one user waits (no queueing behind other cases).
        # Raw probabilities and sources are kept; the threshold and number rule are applied when scoring.
        for case, rows in cases.items():
            if case in out:
                continue
            verdicts, usage = await check_claims([r["claim"] for r in rows], exhibits_of(case), drex, number_rule=False)
            out[case] = {"usage": usage, "verdicts": [v.__dict__ for v in verdicts]}
            path.write_text(json.dumps(out, indent=1))
            print(case, f"{usage['seconds']:.2f}s", usage["input_tokens"], "tokens")


async def run_haiku() -> None:
    """The baseline CTM_BASELINE names: a Claude model through `claude -p`, or a REGISTRY model over an API."""
    from bench.haiku import check_claims_haiku
    from bench.openai_compat import REGISTRY, check_claims_openai, resolve

    api = resolve(BL) if BL in REGISTRY else None
    cases = load_cases()
    path = OUT / f"{BL}_raw{TAG}.json"
    out = json.loads(path.read_text()) if path.exists() else {}
    for case, rows in cases.items():  # one at a time, so seconds is what one user waits for one petition
        if case in out:
            continue
        claims, exhibits = [r["claim"] for r in rows], exhibits_of(case)
        if api:
            verdicts, usage = await check_claims_openai(claims, exhibits, api["model"], api["base_url"], api["key"],
                                                        api["extra"], api["price"])
            usage["thinking"] = api["thinking"]
        else:
            verdicts, usage = await check_claims_haiku(claims, exhibits)
        out[case] = {"usage": usage, "verdicts": verdicts}
        print(case, f"{usage['seconds']:.1f}s", f"${usage['cost_usd']:.4f}")
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1))


async def run_telepathy() -> None:
    """Same claims, same exhibits: all questions against one state in few requests vs one request per claim."""
    case, rows = next(iter(load_cases().items()))
    claims = [r["claim"] for r in rows][:8]
    exhibits = exhibits_of(case)
    state = exhibits_state(exhibits)
    from ctm_drex.check import VERDICT_OPTIONS

    def q(c: str) -> Choice:
        return Choice(f'Is this claim supported, contradicted or unsupported by the exhibits?\n"{c}"', VERDICT_OPTIONS)

    async with Drex() as drex:
        await drex.ask(state, {"warm": q(claims[0])})  # warm the connection
        batched = await drex.ask(state, {f"v{i}": q(c) for i, c in enumerate(claims)})
        singles = []
        for c in claims:  # sequential: the cost of asking one at a time
            singles.append(await drex.ask(state, {"v": q(c)}))
        repeat = await drex.ask(state, {f"v{i}": q(c) for i, c in enumerate(claims)})
    result = {
        "case": case,
        "claims": len(claims),
        "batched": {"seconds": batched.seconds, "input_tokens": batched.input_tokens},
        "one_per_request": {
            "seconds": sum(r.seconds for r in singles),
            "input_tokens": sum(r.input_tokens for r in singles),
        },
        "batched_repeat_same_state": {"seconds": repeat.seconds, "input_tokens": repeat.input_tokens},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "telepathy.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


# ---- scoring ---------------------------------------------------------------------------------------------


def drex_predictions(raw: dict, cases: dict, threshold: float, number_rule: bool) -> dict[str, str]:
    preds = {}
    for case, rows in cases.items():
        exhibits = exhibits_of(case)
        verdicts = raw[case]["verdicts"]
        assert len(rows) == len(verdicts), f"{case}: labels changed since the run; rerun"
        for row, v in zip(rows, verdicts):
            assert row["claim"] == v["claim"], f"{case}: claim order changed; rerun"
            probs = v["probabilities"]
            if not probs:
                preds[row["id"]] = "error"
                continue
            verdict = decide(probs, threshold)
            if number_rule and verdict == "supported":
                if numbers_missing(row["claim"], _cited_text(row["claim"], v["source"], exhibits)):
                    rest = {k: p for k, p in probs.items() if k != "supported"}
                    verdict = max(rest, key=rest.get) if rest else "unsupported"
            preds[row["id"]] = verdict
    return preds


def metrics(rows: list[dict], preds: dict[str, str]) -> dict:
    n = len(rows)
    correct = sum(preds[r["id"]] == r["label"] for r in rows)
    f1s, recalls = [], []
    for label in LABELS:
        tp = sum(preds[r["id"]] == label and r["label"] == label for r in rows)
        fp = sum(preds[r["id"]] == label and r["label"] != label for r in rows)
        fn = sum(preds[r["id"]] != label and r["label"] == label for r in rows)
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        recalls.append(rc)
        f1s.append(2 * p * rc / (p + rc) if p + rc else 0.0)
    bad = [r for r in rows if r["label"] != "supported"]
    good = [r for r in rows if r["label"] == "supported"]
    return {
        "n": n,
        "accuracy": correct / n,
        "macro_f1": sum(f1s) / 3,
        "balanced_accuracy": sum(recalls) / 3,
        # The legal risk: a contradicted or unsupported claim passed as proven and filed.
        "missed_bad_claims": sum(preds[r["id"]] == "supported" for r in bad) / len(bad) if bad else None,
        "false_alarms": sum(preds[r["id"]] != "supported" for r in good) / len(good) if good else 0.0,
        "flag_recall": sum(preds[r["id"]] != "supported" for r in bad) / len(bad) if bad else 0.0,
    }


def fit_threshold(raw: dict, cases: dict, rows: list[dict]) -> float:
    """The one trained parameter: the probability of 'supported' needed to pass a claim, fitted on dev."""
    scored = [(metrics(rows, drex_predictions(raw, cases, t / 100, True))["macro_f1"], t / 100) for t in range(5, 100, 5)]
    top = max(f for f, _ in scored)
    tied = [t for f, t in scored if f == top]
    return tied[len(tied) // 2]  # middle of the best plateau, not its edge


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


def score() -> None:
    cases = load_cases()
    all_rows = [r for rows in cases.values() for r in rows]
    dev = [r for r in all_rows if r["split"] == "dev"]
    test = [r for r in all_rows if r["split"] == "test"]
    test_cases = sorted({r["case"] for r in test})
    for c, rows in cases.items():
        assert len({r["split"] for r in rows}) == 1, f"{c} mixes dev and test claims: exhibits would leak"
    raw = json.loads((OUT / f"drex_raw{TAG}.json").read_text())
    report: dict = {"test_claims": len(test), "dev_claims": len(dev), "test_cases": len(test_cases)}

    t = fit_threshold(raw, cases, dev)
    report["fitted_threshold"] = t
    report["drex_raw"] = metrics(test, drex_predictions(raw, cases, DEFAULT_THRESHOLD, False))
    report["drex_rules"] = metrics(test, drex_predictions(raw, cases, DEFAULT_THRESHOLD, True))
    report["drex_rules_fitted"] = metrics(test, drex_predictions(raw, cases, t, True))

    d_secs = [raw[c]["usage"]["seconds"] for c in test_cases]
    d_tok = sum(raw[c]["usage"]["input_tokens"] for c in test_cases)
    report["drex_speed"] = {"p50_s_per_petition": pct(d_secs, 0.5), "p95_s_per_petition": pct(d_secs, 0.95)}
    report["drex_cost_per_1k_claims_usd"] = d_tok * PRICE_PER_M_INPUT / 1e6 / len(test) * 1000

    hpath = OUT / f"{BL}_raw{TAG}.json"
    if hpath.exists():
        hraw = json.loads(hpath.read_text())
        if all(c in hraw for c in test_cases):
            hpreds, hrules = {}, {}
            for c in test_cases:
                rows, hv = cases[c], hraw[c]["verdicts"]
                assert len(rows) == len(hv) and all(r["claim"] == v["claim"] for r, v in zip(rows, hv)), f"{c}: rerun haiku"
                ex = exhibits_of(c)
                for r, v in zip(rows, hv):
                    hpreds[r["id"]] = v["verdict"]
                    # the same number rule Drex gets, so the comparison is like for like
                    cited = _cited_text(r["claim"], v.get("source") if v.get("source") in ex else None, ex)
                    hrules[r["id"]] = "contradicted" if v["verdict"] == "supported" and numbers_missing(r["claim"], cited) else v["verdict"]
            report[f"{BL}"] = metrics(test, hpreds)
            report[f"{BL}_rules"] = metrics(test, hrules)
            report[f"{BL}_model"] = hraw[test_cases[0]]["usage"].get("model")
            h_secs = [hraw[c]["usage"]["seconds"] for c in test_cases]
            report[f"{BL}_speed"] = {"p50_s_per_petition": pct(h_secs, 0.5), "p95_s_per_petition": pct(h_secs, 0.95)}
            report[f"{BL}_cost_per_1k_claims_usd"] = sum(hraw[c]["usage"]["cost_usd"] for c in test_cases) / len(test) * 1000
            report[f"{BL}_unparsed"] = sum(not v.get("parsed", True) for c in test_cases for v in hraw[c]["verdicts"])
            u0 = hraw[test_cases[0]]["usage"]
            for k in ("price_basis", "thinking"):
                if k in u0:
                    report[f"{BL}_{k}"] = u0[k]
            if "attempts" in u0:
                report[f"{BL}_attempts"] = sum(hraw[c]["usage"]["attempts"] for c in test_cases)

    # Handoff: what the next agent reads. Tokens estimated as characters / 4.
    full, slip = [], []
    for c in test_cases:
        draft = (DATA / "cases" / c / "petition.md").read_text()
        ex = exhibits_state(exhibits_of(c))
        verdicts = [Verdict(**v) for v in raw[c]["verdicts"]]
        slip.append(len(receipt(verdicts, raw[c]["usage"])) / 4)
        full.append((len(draft) + len(ex)) / 4)
    report["handoff_tokens_estimate"] = {"draft_plus_exhibits": statistics.mean(full), "receipt": statistics.mean(slip)}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (f"summary{TAG}.json" if BL == "haiku" else f"summary_{BL}{TAG}.json")).write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    {"drex": lambda: asyncio.run(run_drex()), "haiku": lambda: asyncio.run(run_haiku()),
     "telepathy": lambda: asyncio.run(run_telepathy()), "score": score}[cmd]()
