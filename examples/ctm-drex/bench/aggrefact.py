"""Public benchmark: Drex vs Claude Haiku 5.5 on a sample of LLM-AggreFact (is a claim supported by a document?).

    python -m bench.aggrefact sample <test.parquet>   # write data/aggrefact/sample.jsonl (200 rows, seed 7)
    python -m bench.aggrefact drex                    # Drex on all 200 (dev + test), cache raw answers
    python -m bench.aggrefact haiku                   # Haiku on the 150 test rows, 6 at a time, cached
    python -m bench.aggrefact score                   # fit the threshold on dev, score on test, write summary

LLM-AggreFact (Tang, Laban, Durrett 2024; CC BY-ND 4.0) is human-labelled: label 1 = supported, 0 = unsupported.
The official copy (lytang/LLM-AggreFact) is gated behind a Hugging Face login, so the sample was drawn from the
public, ungated re-upload NinaCalvi/llm_aggrefact_pre_aug9, whose test split has the same 10 sub-datasets and
the same per-dataset counts (12,949 rows) as the official v1 test set.
"""

from __future__ import annotations

import asyncio
import os
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "aggrefact" / "sample.jsonl"
OUT = ROOT / "bench" / "results"
BL = os.environ.get("CTM_BASELINE", "haiku")  # frontier baseline: haiku or opus
RAW = OUT / "aggrefact_raw.json"
SUMMARY = OUT / "aggrefact_summary.json"
if BL != "haiku":
    SUMMARY = SUMMARY.with_name(f"aggrefact_summary_{BL}.json")

SEED = 7
N_DEV = 50
MAX_DOC_CHARS = 6000
PER_DATASET_PER_LABEL = 10  # 10 sub-datasets x 2 labels x 10 = 200

OPTIONS = {
    "supported": "The document states or clearly implies the claim.",
    "unsupported": "The document does not state the claim, or contradicts it.",
}
INSTRUCTIONS = "Is the following claim supported by the document above?\n\nClaim: {claim}"

HAIKU_SYSTEM = "You are a careful fact checker. Answer only in the requested JSON."
HAIKU_PROMPT = """Document:
<<<
{doc}
>>>

Claim: {claim}

Is the claim supported by the document?
- supported: the document states or clearly implies the claim.
- unsupported: the document does not state the claim, or contradicts it.

Reply with JSON only, no prose: {{"verdict": "supported|unsupported"}}"""
HAIKU_CONCURRENCY = 6


# ---------- sample ----------

def make_sample(parquet: str) -> None:
    import pyarrow.parquet as pq

    rows = pq.read_table(parquet, columns=["dataset", "doc", "claim", "label"]).to_pylist()
    rng = random.Random(SEED)
    picked = []
    for ds in sorted({r["dataset"] for r in rows}):
        for label in (0, 1):
            pool = [r for r in rows if r["dataset"] == ds and r["label"] == label and len(r["doc"]) < MAX_DOC_CHARS]
            picked += rng.sample(pool, PER_DATASET_PER_LABEL)
    rng.shuffle(picked)  # ids follow this order, so the first 50 (dev) spread across sub-datasets
    SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLE.open("w") as f:
        for i, r in enumerate(picked):
            f.write(json.dumps({"id": f"af{i:03d}", "dataset": r["dataset"], "doc": r["doc"],
                                "claim": r["claim"], "label": int(r["label"])}) + "\n")
    print(f"wrote {len(picked)} rows to {SAMPLE}")


def load_sample() -> list[dict]:
    rows = [json.loads(l) for l in SAMPLE.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["id"])
    for i, r in enumerate(rows):
        r["split"] = "dev" if i < N_DEV else "test"
    return rows


def load_raw() -> dict:
    raw = json.loads(RAW.read_text()) if RAW.exists() else {}
    raw.setdefault("drex", {})
    raw.setdefault(BL, {})
    return raw


def save_raw(raw: dict, part: str) -> None:
    """Write one model's results, keeping the others': several baselines can run at once.

    Read-modify-write under an exclusive lock, through a per-process temp file, so parallel runs never clobber
    each other's part or a half-written file."""
    import fcntl

    OUT.mkdir(parents=True, exist_ok=True)
    with open(RAW.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_raw()
        current[part] = raw[part]
        tmp = RAW.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(current, indent=1))
        tmp.replace(RAW)


# ---------- Drex ----------

async def run_drex() -> None:
    from ctm_drex.drex import Choice, Drex

    rows = load_sample()
    raw = load_raw()
    todo = [r for r in rows if r["id"] not in raw["drex"]]

    async with Drex() as d:
        async def one(r):
            try:
                res = await d.ask(r["doc"], {"v": Choice(INSTRUCTIONS.format(claim=r["claim"]), OPTIONS)})
            except Exception as e:  # keep going; a failure is recorded, not scored
                return r["id"], {"error": str(e)[:300]}
            a = res.answers.get("v", {})
            probs = {k: float(p) for k, p in (a.get("probabilities") or {}).items()}
            if not probs and a.get("choice") in OPTIONS:
                probs = {a["choice"]: 1.0}
            return r["id"], {"p_supported": probs.get("supported"), "probabilities": probs, "choice": a.get("choice"),
                             "seconds": res.seconds, "input_tokens": res.input_tokens, "model": res.model}

        for rid, rec in await asyncio.gather(*(one(r) for r in todo)):
            raw["drex"][rid] = rec
    save_raw(raw, "drex")
    errs = sum("error" in v for v in raw["drex"].values())
    print(f"drex: {len(raw['drex'])} rows cached, {errs} errors")


# ---------- Haiku ----------

def parse_verdict(text: str) -> str | None:
    m = re.search(r"\{.*?\}", text, re.S)
    if m:
        try:
            v = json.loads(m.group(0)).get("verdict")
            if v in OPTIONS:
                return v
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


async def haiku_one(r: dict) -> dict:
    prompt = HAIKU_PROMPT.format(doc=r["doc"], claim=r["claim"])
    start = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", BL, "--output-format", "json",
        "--tools", "", "--strict-mcp-config", "--setting-sources", "",
        "--system-prompt", HAIKU_SYSTEM,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(prompt.encode())
    seconds = time.perf_counter() - start
    if proc.returncode != 0:
        return {"error": (err.decode() or out.decode())[:300], "seconds": seconds}
    body = json.loads(out)
    text = body.get("result", "") or ""
    usage = body.get("usage") or {}
    return {
        "verdict": parse_verdict(text),
        "text": text[:500],
        "seconds": seconds,
        "input_tokens": sum(int(usage.get(k) or 0) for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cost_usd": float(body.get("total_cost_usd") or 0.0),
        "model": next(iter(body.get("modelUsage") or {}), BL),
    }


async def run_haiku(split: str = "test") -> None:
    """The baseline CTM_BASELINE names: a Claude model through `claude -p`, or a REGISTRY model over an API."""
    import httpx

    from bench.openai_compat import REGISTRY, aggrefact_one_openai, resolve

    api = resolve(BL) if BL in REGISTRY else None
    client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) if api else None
    rows = [r for r in load_sample() if split == "all" or r["split"] == split]
    raw = load_raw()
    todo = [r for r in rows if r["id"] not in raw[BL] or "error" in raw[BL][r["id"]]]
    sem = asyncio.Semaphore(HAIKU_CONCURRENCY)
    done = 0

    async def one(r):
        nonlocal done
        async with sem:
            if api:
                rec = await aggrefact_one_openai(r, api["model"], api["base_url"], api["key"], api["extra"],
                                                 api["price"], client)
                rec["thinking"] = api["thinking"]
            else:
                rec = await haiku_one(r)
        raw[BL][r["id"]] = rec
        done += 1
        if done % 10 == 0:
            save_raw(raw, BL)
            print(f"{BL} {done}/{len(todo)}", flush=True)

    try:
        await asyncio.gather(*(one(r) for r in todo))
    finally:
        if client:
            await client.aclose()
    save_raw(raw, BL)
    errs = sum("error" in v for v in raw[BL].values())
    print(f"{BL}: {len(raw[BL])} rows cached, {errs} errors")


# ---------- score ----------

def balanced_accuracy(labels: list[int], preds: list[int]) -> float:
    pos = [p for l, p in zip(labels, preds) if l == 1]
    neg = [p for l, p in zip(labels, preds) if l == 0]
    tpr = sum(pos) / len(pos) if pos else 0.0
    tnr = sum(1 - p for p in neg) / len(neg) if neg else 0.0
    return (tpr + tnr) / 2


def fit_threshold(ps: list[float], labels: list[int]) -> float:
    """Threshold on p(supported) maximising balanced accuracy; ties go to the threshold nearest 0.5."""
    cands = sorted(set(ps))
    mids = [(a + b) / 2 for a, b in zip(cands, cands[1:])] + [cands[0] - 1e-9, cands[-1] + 1e-9]
    return max(mids, key=lambda t: (balanced_accuracy(labels, [int(p >= t) for p in ps]), -abs(t - 0.5)))


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]


def score() -> dict:
    from ctm_drex.drex import PRICE_PER_M_INPUT

    rows = load_sample()
    raw = load_raw()
    by_id = {r["id"]: r for r in rows}
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]

    def drex_ok(r):
        v = raw["drex"].get(r["id"], {})
        return v.get("p_supported") is not None

    dev_ok = [r for r in dev if drex_ok(r)]
    thr = fit_threshold([raw["drex"][r["id"]]["p_supported"] for r in dev_ok], [r["label"] for r in dev_ok])
    test_ok = [r for r in test if drex_ok(r)]
    ps = [raw["drex"][r["id"]]["p_supported"] for r in test_ok]
    labels = [r["label"] for r in test_ok]
    drex_secs = [raw["drex"][r["id"]]["seconds"] for r in test_ok]
    drex_tok = [raw["drex"][r["id"]]["input_tokens"] for r in test_ok]

    h_rows = [r for r in test if r["id"] in raw[BL] and "error" not in raw[BL][r["id"]]]
    h_fail = [r for r in h_rows if raw[BL][r["id"]]["verdict"] is None]
    # a parse failure counts as "unsupported" (the conservative default), and is reported separately
    h_preds = [int(raw[BL][r["id"]]["verdict"] == "supported") for r in h_rows]
    h_labels = [r["label"] for r in h_rows]
    h_secs = [raw[BL][r["id"]]["seconds"] for r in h_rows]
    h_cost = [raw[BL][r["id"]]["cost_usd"] for r in h_rows]
    h_tok = [raw[BL][r["id"]]["input_tokens"] for r in h_rows]

    # both models on the same rows, for a like-for-like number
    both = [r for r in test_ok if r in h_rows]

    per_ds = {}
    for ds in sorted({r["dataset"] for r in test}):
        rs = [r for r in both if r["dataset"] == ds]
        if rs:
            per_ds[ds] = {
                "n": len(rs),
                "drex_fitted": round(balanced_accuracy([r["label"] for r in rs],
                                                       [int(raw["drex"][r["id"]]["p_supported"] >= thr) for r in rs]), 3),
                BL: round(balanced_accuracy([r["label"] for r in rs],
                                                 [int(raw[BL][r["id"]]["verdict"] == "supported") for r in rs]), 3),
            }

    mean_drex_cost = statistics.mean(drex_tok) * PRICE_PER_M_INPUT / 1e6 if drex_tok else None
    summary = {
        "benchmark": "LLM-AggreFact v1 test (human labels), stratified sample",
        "source": "huggingface.co/datasets/NinaCalvi/llm_aggrefact_pre_aug9 (public re-upload of lytang/LLM-AggreFact, which is gated)",
        "licence": "CC BY-ND 4.0 (LLM-AggreFact)",
        "seed": SEED,
        "n_sample": len(rows), "n_dev": len(dev), "n_test": len(test),
        "sub_datasets": sorted({r["dataset"] for r in rows}),
        "drex": {
            "model": next((v.get("model") for v in raw["drex"].values() if v.get("model")), None),
            "n_test_scored": len(test_ok),
            "errors_all": sum("error" in v for v in raw["drex"].values()),
            "balanced_accuracy_thr_0.5": round(balanced_accuracy(labels, [int(p >= 0.5) for p in ps]), 3),
            "fitted_threshold": round(thr, 4),
            "balanced_accuracy_fitted": round(balanced_accuracy(labels, [int(p >= thr) for p in ps]), 3),
            "seconds_p50": pct(drex_secs, 0.5), "seconds_p95": pct(drex_secs, 0.95),
            "mean_input_tokens": round(statistics.mean(drex_tok), 1) if drex_tok else None,
            "cost_per_1000_claims_usd": round(mean_drex_cost * 1000, 4) if mean_drex_cost is not None else None,
            "price_basis": f"${PRICE_PER_M_INPUT}/M input tokens (Drex Max 1.1 published price), output free",
            "latency_note": "one request per claim, up to 16 in flight; seconds = HTTP round trip per request",
        },
        BL: {
            "model": next((v.get("model") for v in raw[BL].values() if v.get("model")), None),
            "n_test_scored": len(h_rows),
            "errors": sum(1 for r in test if "error" in raw[BL].get(r["id"], {})),
            "parse_failures": len(h_fail),
            "balanced_accuracy": round(balanced_accuracy(h_labels, h_preds), 3) if h_rows else None,
            "seconds_p50": pct(h_secs, 0.5), "seconds_p95": pct(h_secs, 0.95),
            "mean_input_tokens": round(statistics.mean(h_tok), 1) if h_tok else None,
            "cost_per_1000_claims_usd": round(statistics.mean(h_cost) * 1000, 4) if h_cost else None,
            "latency_note": ("HTTP call per claim incl. retries, 6 concurrent" if any("price_basis" in raw[BL][r["id"]] for r in h_rows)
                             else "claude -p per claim (includes CLI start-up), 6 concurrent"),
            **{k: next((raw[BL][r["id"]][k] for r in h_rows if k in raw[BL][r["id"]]), None)
               for k in ("price_basis", "thinking") if any(k in raw[BL][r["id"]] for r in h_rows)},
            "mean_output_tokens": round(statistics.mean(raw[BL][r["id"]].get("output_tokens", 0) for r in h_rows), 1) if h_rows else None,
        },
        "n_both_scored": len(both),
        "per_dataset_balanced_accuracy_test": per_ds,
    }
    for k in ("seconds_p50", "seconds_p95"):
        for m in ("drex", BL):
            if summary[m][k] is not None:
                summary[m][k] = round(summary[m][k], 3)
    OUT.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "sample":
        make_sample(sys.argv[2])
    elif cmd == "drex":
        asyncio.run(run_drex())
    elif cmd in ("haiku", BL):  # "haiku" runs whichever baseline CTM_BASELINE names
        asyncio.run(run_haiku(sys.argv[2] if len(sys.argv) > 2 else "test"))
    elif cmd == "score":
        score()
    else:
        sys.exit(__doc__)
