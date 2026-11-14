"""Fill the deck template from the benchmark results and write deck/deck.html (and optionally the PDF).

    python deck/fill.py            # reads bench/results/*.json, writes deck/deck.html
    python deck/fill.py --check    # also lists keys that came out missing
    python deck/fill.py --pdf      # also prints deck/CTM-Drex.pdf with headless Chrome

Every number on the slides comes from here; none is typed into index.html. The template has two kinds of
placeholder:

  {{key}}     a formatted scalar (seconds, dollars, counts, ratios)
  [[block]]   a generated HTML/SVG fragment (charts, comparison cards, the receipt excerpt)

A value whose source file or field is missing becomes an em dash, and a chart drops the missing model, so the
deck never shows an invented number.

Models are discovered, not listed: every bench/results/aggrefact_summary*.json and summary*.json adds the
baseline it holds. A new DeepInfra (di-*) or OpenRouter (or-*) run shows up on the next `python deck/fill.py`.

"Bad claims passed" on LLM-AggreFact is counted here from bench/results/aggrefact_raw.json and
data/aggrefact/sample.jsonl: the label-0 (unsupported) rows of the test split (rows after the first 50 by id,
as bench/aggrefact.py splits them) that a system called supported. Drex is counted at its fitted threshold from
the summary, and at 0.5 for the caveat.

PDF command (16:9, 1600x900 pages):
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-pdf-header-footer \
      --virtual-time-budget=8000 --print-to-pdf=deck/CTM-Drex.pdf file://.../deck/deck.html
"""

from __future__ import annotations

import html
import json
import math
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
SAMPLE = ROOT / "data" / "aggrefact" / "sample.jsonl"
# the demo receipt: the newest of run3, run2 (each keeps every round: Round 1, Round 2, ...)
RECEIPT = next((p for p in (ROOT / "demo" / r / "ctm-receipt.md" for r in ("run3", "run2")) if p.exists()),
               ROOT / "demo" / "run3" / "ctm-receipt.md")
FULL = RESULTS / "aggrefact_full_summary.json"
NDI = RESULTS / "ndi_parse.json"
CASES_PDF = RESULTS / "summary_cases_pdf.json"
DECK = Path(__file__).resolve().parent
TEMPLATE = DECK / "index.html"
OUT = DECK / "deck.html"
PDF = DECK / "CTM-Drex.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
N_DEV = 50  # bench/aggrefact.py: the first 50 rows by id are dev, the rest test

MISSING = chr(0x2014)
LAV, INDIGO, DARK, INK, MUTED, GRID = "#b3a8ee", "#4b2bb5", "#22145a", "#141414", "#6b6880", "#e4e0f5"

NAMES = {
    "drex": "Drex Max 1.1",
    "haiku": "Claude Haiku 5.5",
    "opus": "Claude Opus 5.5",
    "di-gemini-3.1-pro": "Gemini 3.1 Pro",
    "di-kimi-k3": "Kimi K3",
    "di-qwen3.8-max": "Qwen3.8-Max",
    "di-qwen3.8-27b": "Qwen3.8-27B",
    "di-gemma-4-31b": "Gemma 4 31B",
    "di-llama-3.3-70b": "Llama 3.3 70B",
    "di-gpt-oss-120b": "gpt-oss-120b",
    "di-deepseek-v4-pro": "DeepSeek V4 Pro",
    "or-grok-4.7": "Grok 4.7",
    "or-gemini-3.8-flash": "Gemini 3.8 Flash",
    "or-gpt-5.5": "GPT-5.5",
    "or-gpt-5.6-sol": "GPT-5.6 Sol",
}


# ---------------------------------------------------------------- helpers
def esc(text: str) -> str:
    """HTML-escape and make control, format and bidi characters visible, so file text can't forge markup."""
    out = []
    for ch in str(text):
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Co", "Cs") and ch not in "\n\t":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return html.escape("".join(out))


def name(key: str, model: str | None = None) -> str:
    if key in NAMES:
        return NAMES[key]
    base = (model or key).split("/")[-1]
    return base


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def sig(x: float, digits: int = 2) -> str:
    """Round to significant digits for ratios: 799.3 -> 800, 33.6 -> 34, 5.98 -> 6.0."""
    if x >= 100:
        p = 10 ** (int(math.floor(math.log10(x))) - digits + 1)
        return f"{round(x / p) * p:,.0f}"
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}"


def usd(x: float | None) -> str:
    if x is None:
        return MISSING
    if x >= 1:
        return f"${x:.2f}"
    if x >= 0.1:
        return f"${x:.2f}"
    if x >= 0.01:
        return f"${x:.3f}"
    return f"${x:.2g}" if x >= 0.001 else f"${x:.5f}".rstrip("0")


def secs(x: float | None) -> str:
    if x is None:
        return MISSING
    return f"{x:.2f} s" if x < 10 else f"{x:.1f} s"


# ---------------------------------------------------------------- data
def aggrefact_models() -> tuple[dict, dict]:
    """Return (models, meta). models[key] = label, bacc, p50, cost, missed, n_neg."""
    rows = [json.loads(l) for l in SAMPLE.read_text().splitlines() if l.strip()] if SAMPLE.exists() else []
    rows.sort(key=lambda r: r["id"])
    neg = [r["id"] for r in rows[N_DEV:] if r.get("label") == 0]
    raw = load(RESULTS / "aggrefact_raw.json")
    models: dict[str, dict] = {}
    meta: dict = {"n_test": None, "n_neg": len(neg) or None, "thr": None, "drex_bacc_05": None}
    for f in sorted(RESULTS.glob("aggrefact_summary*.json")):
        d = load(f)
        meta["n_test"] = meta["n_test"] or num(d.get("n_test"))
        for key, v in d.items():
            if not isinstance(v, dict) or "model" not in v:
                continue
            if key == "drex":
                if "drex" in models:
                    continue
                thr = num(v.get("fitted_threshold"))
                meta["thr"], meta["drex_bacc_05"] = thr, num(v.get("balanced_accuracy_thr_0.5"))
                r = raw.get("drex", {})
                ok = neg and all(num(r.get(i, {}).get("p_supported")) is not None for i in neg)
                models["drex"] = {
                    "label": name("drex"),
                    "bacc": num(v.get("balanced_accuracy_fitted")),
                    "p50": num(v.get("seconds_p50")),
                    "cost": num(v.get("cost_per_1000_claims_usd")),
                    "missed": sum(r[i]["p_supported"] >= thr for i in neg) if ok and thr is not None else None,
                    "missed_05": sum(r[i]["p_supported"] >= 0.5 for i in neg) if ok else None,
                }
            else:
                r = raw.get(key, {})
                ok = neg and all(i in r and "error" not in r[i] for i in neg)
                models[key] = {
                    "label": name(key, v.get("model")),
                    "bacc": num(v.get("balanced_accuracy")),
                    "p50": num(v.get("seconds_p50")),
                    "cost": num(v.get("cost_per_1000_claims_usd")),
                    "missed": sum(r[i].get("verdict") == "supported" for i in neg) if ok else None,
                }
    return models, meta


def petition_models() -> tuple[dict, dict]:
    """Synthetic O-1A cases (bench/run.py). models[key] = label, bacc, p50 (s per petition), cost (per 1k claims)."""
    models: dict[str, dict] = {}
    meta: dict = {}
    for f in sorted(RESULTS.glob("summary*.json")):
        if f.name.startswith("summary_cases"):  # the NDI-parsed-exhibit rerun, not a baseline
            continue
        d = load(f)
        if not meta:
            meta = {k: num(d.get(k)) for k in ("test_claims", "test_cases")}
            meta["handoff"] = d.get("handoff_tokens_estimate", {})
        if "drex" not in models and "drex_speed" in d:
            models["drex"] = {
                "label": name("drex"),
                "bacc": num(d.get("drex_rules_fitted", {}).get("balanced_accuracy")),
                "p50": num(d["drex_speed"].get("p50_s_per_petition")),
                "cost": num(d.get("drex_cost_per_1k_claims_usd")),
            }
        for k, v in d.items():
            if k.endswith("_speed") and k != "drex_speed":
                key = k[: -len("_speed")]
                models[key] = {
                    "label": name(key, d.get(f"{key}_model")),
                    "bacc": num(d.get(key, {}).get("balanced_accuracy")),
                    "p50": num(v.get("p50_s_per_petition")),
                    "cost": num(d.get(f"{key}_cost_per_1k_claims_usd")),
                }
    return models, meta


def receipt_round(lines: list[str]) -> dict:
    head = next((l for l in lines if l.startswith("CTM receipt")), "")
    m = re.search(r"(\d+) claims checked by (\S+) in ([\d.]+)s", head)
    items = []
    for l in lines:
        mm = re.match(r"\s*(\d+)\.\s+(PROVEN|CONTRADICTED|CLIENT-STATED ONLY)\s+(?:\[([^\]]+)\]\s*)?(.*)", l)
        if mm:
            items.append({"n": int(mm.group(1)), "verdict": mm.group(2), "exhibit": mm.group(3), "text": mm.group(4)})
    foot = next((x for l in lines if (x := re.match(r"(\d+) claim\(s\) need evidence or a rewrite before filing(?:; (\d+) already marked)?", l))), None)
    return {
        "round": next((l.strip() for l in lines if l.startswith("Round")), ""),
        "claims": int(m.group(1)) if m else None,
        "model": m.group(2) if m else None,
        "seconds": float(m.group(3)) if m else None,
        "items": items,
        "flagged": int(foot.group(1)) if foot else None,
        "marked": int(foot.group(2)) if foot and foot.group(2) else None,
        "contradicted": sum(i["verdict"] == "CONTRADICTED" for i in items),
    }


def receipt(path: Path) -> list[dict]:
    """All rounds of a CTM receipt, in order. A file without "Round" headers is one round."""
    if not path.exists():
        return []
    rounds, cur = [], []
    for l in path.read_text().splitlines():
        if l.startswith("Round") and cur:
            rounds.append(cur)
            cur = []
        cur.append(l)
    if cur:
        rounds.append(cur)
    return [r for r in (receipt_round(c) for c in rounds) if r["items"]]


# ---------------------------------------------------------------- charts
def text_w(s: str, size: float) -> float:
    return len(s) * size * 0.56


def scatter(models: dict) -> str:
    """Accuracy vs cost: log-x cost per 1,000 claims, y balanced accuracy; one dot per model, Drex highlighted."""
    pts = [(k, m) for k, m in models.items() if m["bacc"] is not None and m["cost"]]
    if not pts:
        return f'<div class="missing">{MISSING}</div>'
    W, H, L, R, T, B = 1000, 470, 86, 30, 16, 66
    xs = [math.log10(m["cost"]) for _, m in pts]
    ys = [m["bacc"] for _, m in pts]
    x0, x1 = math.floor(min(xs)), math.ceil(max(xs))
    y0 = math.floor((min(ys) - 0.01) * 50) / 50
    y1 = math.ceil((max(ys) + 0.01) * 50) / 50
    px = lambda v: L + (math.log10(v) - x0) / (x1 - x0) * (W - L - R)
    py = lambda v: T + (y1 - v) / (y1 - y0) * (H - T - B)
    g = [f'<svg viewBox="0 0 {W} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    # grid + axes
    y = y0
    while y <= y1 + 1e-9:
        g.append(f'<line x1="{L}" x2="{W-R}" y1="{py(y):.1f}" y2="{py(y):.1f}" stroke="{GRID}"/>')
        g.append(f'<text x="{L-12}" y="{py(y)+6:.1f}" text-anchor="end" class="tick">{y:.2f}</text>')
        y = round(y + 0.02, 4)
    for e in range(x0, x1 + 1):
        v = 10 ** e
        lab = f"${v:g}" if v >= 1 else f"${v:.{-e}f}"
        g.append(f'<line x1="{px(v):.1f}" x2="{px(v):.1f}" y1="{T}" y2="{H-B}" stroke="{GRID}"/>')
        g.append(f'<text x="{px(v):.1f}" y="{H-B+28}" text-anchor="middle" class="tick">{lab}</text>')
    g.append(f'<text x="{(L+W-R)/2:.0f}" y="{H-8}" text-anchor="middle" class="axis">Cost per 1,000 claims (log scale) → cheaper to the left</text>')
    g.append(f'<text transform="translate(18 {(T+H-B)/2:.0f}) rotate(-90)" text-anchor="middle" class="axis">Balanced accuracy</text>')
    # labels with greedy collision avoidance
    boxes: list[tuple[float, float, float, float]] = []
    def free(b):
        return all(b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3] for o in boxes) and b[0] > L and b[2] < W - R + 4 and b[1] > T - 4 and b[3] < H - B
    order = sorted(pts, key=lambda p: p[0] != "drex")
    dots, labels = [], []
    for k, m in order:
        cx, cy = px(m["cost"]), py(m["bacc"])
        is_d = k == "drex"
        r = 13 if is_d else 8
        boxes.append((cx - r, cy - r, cx + r, cy + r))
        dots.append((k, cx, cy, r))
    for k, cx, cy, r in dots:
        is_d = k == "drex"
        size = 21 if is_d else 16
        lab = models[k]["label"]
        w, h = text_w(lab, size) + (0 if not is_d else 8), size + 4
        for dx, dy, anchor in [(r + 8, 0, "start"), (-(r + 8), 0, "end"), (0, -(r + 14), "middle"), (0, r + 20, "middle"),
                               (r + 6, -(r + 10), "start"), (r + 6, r + 16, "start"), (-(r + 6), -(r + 10), "end"), (-(r + 6), r + 16, "end")]:
            bx = cx + dx - (w if anchor == "end" else w / 2 if anchor == "middle" else 0)
            b = (bx, cy + dy - h / 2 - 2, bx + w, cy + dy + h / 2 - 2)
            if free(b):
                boxes.append(b)
                labels.append((k, cx + dx, cy + dy + size * 0.35, anchor, size, lab))
                break
        else:
            labels.append((k, cx + r + 8, cy + size * 0.35, "start", size, lab))
    for k, cx, cy, r in dots:
        if k == "drex":
            g.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r+9}" fill="{INDIGO}" opacity=".14"/>')
            g.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{INDIGO}"/>')
        else:
            g.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{LAV}" stroke="#8f80e0" stroke-width="1.5"/>')
    for k, x, y, anchor, size, lab in labels:
        cls = "lab drex" if k == "drex" else "lab"
        g.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="{cls}">{esc(lab)}</text>')
    g.append("</svg>")
    return "".join(g)


def hbars(rows: list[tuple[str, float, str, bool]], width: int = 640, row_h: int = 40, label_w: int = 200,
          value_w: int = 90) -> str:
    """Horizontal bars. rows = (label, value, shown text, highlighted). Highlighted = indigo, others lavender."""
    if not rows:
        return f'<div class="missing">{MISSING}</div>'
    H = row_h * len(rows)
    vmax = max(v for _, v, _, _ in rows) or 1
    span = width - label_w - value_w
    g = [f'<svg viewBox="0 0 {width} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for i, (lab, v, shown, hi) in enumerate(rows):
        y = i * row_h
        bw = max(3, v / vmax * span)
        fill = INDIGO if hi else LAV
        weight = ' font-weight="700"' if hi else ""
        g.append(f'<text x="{label_w-12}" y="{y+row_h/2+6}" text-anchor="end" class="blab"{weight}>{esc(lab)}</text>')
        g.append(f'<rect x="{label_w}" y="{y+7}" width="{bw:.1f}" height="{row_h-14}" rx="4" fill="{fill}"/>')
        g.append(f'<text x="{label_w+bw+10:.1f}" y="{y+row_h/2+6}" class="bval"{weight}>{esc(shown)}</text>')
    g.append("</svg>")
    return "".join(g)


def vbars(groups: list[tuple[str, list[tuple[float, str, bool]]]], width=520, height=380) -> str:
    """Vertical grouped bars in the old deck's style: lavender = baseline, indigo = ours, value on top."""
    vals = [v for _, bars in groups for v, _, _ in bars if v is not None]
    if not vals:
        return f'<div class="missing">{MISSING}</div>'
    vmax = max(vals)
    base, top = height - 70, 50
    gw = (width - 20) / len(groups)
    bw = 84
    g = [f'<svg viewBox="0 0 {width} {height}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    g.append(f'<line x1="0" x2="{width}" y1="{base}" y2="{base}" stroke="#cfc8f2" stroke-width="2"/>')
    for gi, (lab, bars) in enumerate(groups):
        cx = 10 + gw * gi + gw / 2
        n = len(bars)
        start = cx - (n * bw + (n - 1) * 12) / 2
        for bi, (v, shown, hi) in enumerate(bars):
            x = start + bi * (bw + 12)
            h = max(4, (v or 0) / vmax * (base - top))
            g.append(f'<rect x="{x:.1f}" y="{base-h:.1f}" width="{bw}" height="{h:.1f}" rx="3" fill="{INDIGO if hi else LAV}"/>')
            g.append(f'<text x="{x+bw/2:.1f}" y="{base-h-12:.1f}" text-anchor="middle" class="vval">{esc(shown)}</text>')
        for li, part in enumerate(lab.split("|")):
            g.append(f'<text x="{cx:.1f}" y="{base+32+li*26}" text-anchor="middle" class="{"vlab" if li == 0 else "vsub"}">{esc(part)}</text>')
    g.append("</svg>")
    return "".join(g)


# ---------------------------------------------------------------- assemble
def build() -> tuple[dict[str, str], dict[str, str]]:
    v: dict[str, str] = {}
    b: dict[str, str] = {}
    agg, ameta = aggrefact_models()
    pet, pmeta = petition_models()
    d = agg.get("drex", {})

    # --- AggreFact scalars
    n_test, n_neg = ameta["n_test"], ameta["n_neg"]
    v["agg_n"] = f"{n_test:.0f}" if n_test else MISSING
    v["agg_n_neg"] = f"{n_neg}" if n_neg else MISSING
    v["agg_n_models"] = str(len(agg)) if agg else MISSING
    v["agg_thr"] = f"{ameta['thr']:.2f}" if ameta["thr"] is not None else MISSING
    v["agg_drex_bacc"] = f"{d['bacc']:.3f}" if d.get("bacc") is not None else MISSING
    v["agg_drex_bacc_05"] = f"{ameta['drex_bacc_05']:.3f}" if ameta["drex_bacc_05"] is not None else MISSING
    v["agg_drex_missed"] = str(d["missed"]) if d.get("missed") is not None else MISSING
    v["agg_drex_missed_05"] = str(d["missed_05"]) if d.get("missed_05") is not None else MISSING
    v["agg_drex_p50"] = secs(d.get("p50"))
    v["agg_drex_cost"] = usd(d.get("cost"))
    others = {k: m for k, m in agg.items() if k != "drex"}
    om = [m["missed"] for m in others.values() if m["missed"] is not None]
    v["agg_others_missed_range"] = f"{min(om)}–{max(om)}" if om else MISSING
    above = [m["bacc"] - d["bacc"] for m in others.values() if m["bacc"] is not None and d.get("bacc") is not None and m["bacc"] > d["bacc"]]
    v["agg_n_above"] = str(len(above)) if d.get("bacc") is not None else MISSING
    v["agg_n_others"] = str(len(others))
    v["agg_max_gap"] = f"{max(above):.3f}" if above else "0"
    cheapest_other = min((m["cost"] for m in others.values() if m["cost"]), default=None)
    v["agg_next_cheapest_x"] = sig(cheapest_other / d["cost"]) if cheapest_other and d.get("cost") else MISSING
    for key, tag in [("opus", "opus"), ("haiku", "haiku"), ("di-gemini-3.1-pro", "gemini")]:
        m = agg.get(key)
        v[f"agg_{tag}_cost_x"] = sig(m["cost"] / d["cost"]) if m and m["cost"] and d.get("cost") else MISSING
        v[f"agg_{tag}_speed_x"] = sig(m["p50"] / d["p50"]) if m and m["p50"] and d.get("p50") else MISSING

    cheapest = bool(d.get("cost")) and all(m["cost"] is None or d["cost"] < m["cost"] for m in others.values())
    fastest = bool(d.get("p50")) and all(m["p50"] is None or d["p50"] < m["p50"] for m in others.values())
    v["agg_position"] = ("cheapest and fastest of all" if cheapest and fastest else "the cheapest of all" if cheapest
                         else "the fastest of all" if fastest else "near the cheap end") + f" {len(agg)}"
    b["agg_scatter"] = scatter(agg)

    # comparison cards: Drex, Opus, Gemini, Haiku
    cards = []
    for key in ["drex", "opus", "di-gemini-3.1-pro", "haiku"]:
        m = agg.get(key)
        if not m:
            continue
        dark = " dark" if key == "drex" else ""
        missed = f'{m["missed"]}<small> of {n_neg}</small>' if m.get("missed") is not None else MISSING
        cards.append(
            f'<div class="mcard{dark}"><div class="mname">{esc(m["label"])}</div>'
            f'<div class="mrow"><span>Balanced accuracy</span><b>{m["bacc"]:.3f}</b></div>'
            f'<div class="mrow"><span>Bad claims passed</span><b>{missed}</b></div>'
            f'<div class="mrow"><span>Seconds per claim, p50</span><b>{secs(m["p50"])}</b></div>'
            f'<div class="mrow"><span>$ per 1,000 claims</span><b>{usd(m["cost"])}</b></div></div>'
        )
    b["agg_cards"] = "".join(cards) or MISSING
    rows = sorted(((m["label"], m["missed"], str(m["missed"]), k == "drex") for k, m in agg.items() if m["missed"] is not None),
                  key=lambda r: (r[1], not r[3]))
    b["agg_missed_bars"] = hbars(rows, width=560, row_h=min(34, 430 // max(1, len(rows))), label_w=190, value_w=50)
    # headline wording follows the data: fewest, tied for fewest, or neither
    if d.get("missed") is not None and om:
        best = min(om)
        ties = [m["label"] for m in others.values() if m["missed"] == d["missed"]]
        if d["missed"] < best:
            v["agg_fewest_phrase"] = "the fewest bad claims let through"
        elif d["missed"] == best:
            v["agg_fewest_phrase"] = "tied for the fewest bad claims let through"
        else:
            v["agg_fewest_phrase"] = f"{d['missed']} of {n_neg} bad claims let through"
        v["agg_fewest_note"] = (f"Tied with {esc(', '.join(ties))}, which costs {sig(min(m['cost'] for m in others.values() if m['missed'] == d['missed']) / d['cost'])}× more per claim. "
                                if ties and d.get("cost") else "")
    else:
        v["agg_fewest_phrase"], v["agg_fewest_note"] = MISSING, ""

    # --- petition benchmark
    pd = pet.get("drex", {})
    tc, tcases = pmeta.get("test_claims"), pmeta.get("test_cases")
    v["pet_n"] = f"{tc:.0f}" if tc else MISSING
    v["pet_cases"] = f"{tcases:.0f}" if tcases else MISSING
    v["pet_n_models"] = str(len(pet))
    perfect = [m for m in pet.values() if m["bacc"] is not None and m["bacc"] >= 0.9995]
    v["pet_n_perfect"] = str(len(perfect))
    v["pet_drex_bacc"] = f"{pd['bacc']:.2f}" if pd.get("bacc") is not None else MISSING
    v["pet_drex_p50"] = secs(pd.get("p50"))
    v["pet_opus_p50"] = secs(pet.get("opus", {}).get("p50"))
    v["pet_opus_speed_x"] = sig(pet["opus"]["p50"] / pd["p50"]) if pet.get("opus") and pd.get("p50") else MISSING
    fastest_other = min((m["p50"] for k, m in pet.items() if k != "drex" and m["p50"]), default=None)
    v["pet_next_fastest_x"] = sig(fastest_other / pd["p50"]) if fastest_other and pd.get("p50") else MISSING
    v["pet_next_fastest"] = next((esc(m["label"]) for k, m in pet.items() if k != "drex" and m["p50"] == fastest_other), MISSING)
    cheaper = [m["label"] for k, m in pet.items() if k != "drex" and m["cost"] and pd.get("cost") and m["cost"] < pd["cost"]]
    v["pet_cheaper_than_drex"] = esc(", ".join(cheaper)) if cheaper else "none"
    rows = sorted(((m["label"], m["p50"], secs(m["p50"]), k == "drex") for k, m in pet.items() if m["p50"]), key=lambda r: r[1])
    b["pet_speed_bars"] = hbars(rows, width=600, row_h=min(36, 540 // max(1, len(rows))), label_w=180, value_w=80)
    rows = sorted(((m["label"], m["cost"], usd(m["cost"]), k == "drex") for k, m in pet.items() if m["cost"]), key=lambda r: r[1])
    b["pet_cost_bars"] = hbars(rows, width=600, row_h=min(36, 540 // max(1, len(rows))), label_w=180, value_w=80)

    # per-petition cost (claims per petition = test claims / test cases)
    cpp = tc / tcases if tc and tcases else None
    v["claims_per_petition"] = f"{cpp:.0f}" if cpp else MISSING
    def per_pet(m):
        return m["cost"] * cpp / 1000 if m and m.get("cost") and cpp else None
    v["pp_drex"] = usd(per_pet(pd))
    v["pp_opus"] = usd(per_pet(pet.get("opus")))
    v["pp_opus_x"] = sig(pet["opus"]["cost"] / pd["cost"]) if pet.get("opus") and pd.get("cost") else MISSING
    if pd.get("cost") and cpp:
        v["pp_drex_per_1k_petitions"] = usd(per_pet(pd) * 1000)
    else:
        v["pp_drex_per_1k_petitions"] = MISSING
    groups = []
    for key in ["opus", "di-gemini-3.1-pro", "haiku", "drex"]:
        m = pet.get(key)
        if m and per_pet(m) is not None:
            groups.append((m["label"].replace("Claude ", "") + "|per petition", [(per_pet(m), usd(per_pet(m)), key == "drex")]))
    b["pp_bars"] = vbars(groups, width=820, height=380)

    # --- telepathy
    t = load(RESULTS / "telepathy.json")
    tb, ts, tr = t.get("batched", {}), t.get("one_per_request", {}), t.get("batched_repeat_same_state", {})
    v["tele_claims"] = str(t["claims"]) if num(t.get("claims")) else MISSING
    v["tele_batched_s"] = secs(num(tb.get("seconds")))
    v["tele_singles_s"] = secs(num(ts.get("seconds")))
    v["tele_repeat_s"] = secs(num(tr.get("seconds")))
    v["tele_batched_tok"] = f"{tb['input_tokens']:,}" if num(tb.get("input_tokens")) else MISSING
    v["tele_singles_tok"] = f"{ts['input_tokens']:,}" if num(ts.get("input_tokens")) else MISSING
    v["tele_speed_x"] = sig(ts["seconds"] / tb["seconds"]) if num(ts.get("seconds")) and num(tb.get("seconds")) else MISSING
    v["tele_tok_x"] = sig(ts["input_tokens"] / tb["input_tokens"]) if num(ts.get("input_tokens")) and num(tb.get("input_tokens")) else MISSING
    if num(ts.get("seconds")) and num(tb.get("seconds")):
        b["tele_sec_bars"] = vbars([(f"One request per claim|{v['tele_claims']} requests", [(ts["seconds"], secs(ts["seconds"]), False)]),
                                    (f"All {v['tele_claims']} in one request|1 request", [(tb["seconds"], secs(tb["seconds"]), True)])], width=500, height=380)
    else:
        b["tele_sec_bars"] = MISSING
    if num(ts.get("input_tokens")) and num(tb.get("input_tokens")):
        b["tele_tok_bars"] = vbars([(f"One request per claim|{v['tele_claims']} requests", [(ts["input_tokens"], f"{ts['input_tokens']/1000:.1f}k", False)]),
                                    (f"All {v['tele_claims']} in one request|1 request", [(tb["input_tokens"], f"{tb['input_tokens']/1000:.1f}k", True)])], width=500, height=380)
    else:
        b["tele_tok_bars"] = MISSING

    # --- demo receipt (first and last round)
    rounds = receipt(RECEIPT)
    r1 = rounds[0] if rounds else {}
    rl = rounds[-1] if len(rounds) > 1 else {}
    v["rc_run"] = esc(RECEIPT.parent.name)
    v["rc_rounds"] = str(len(rounds)) if rounds else MISSING
    v["rc_claims"] = str(r1["claims"]) if r1.get("claims") else MISSING
    v["rc_model"] = esc(r1.get("model") or MISSING)
    v["r1_seconds"] = secs(r1.get("seconds"))
    v["r1_flagged"] = str(r1["flagged"]) if r1.get("flagged") is not None else MISSING
    v["r1_contradicted"] = str(r1["contradicted"]) if r1 else MISSING
    v["rl_round"] = esc(rl.get("round", "")) or MISSING
    v["rl_seconds"] = secs(rl.get("seconds"))
    v["rl_flagged"] = str(rl["flagged"]) if rl.get("flagged") is not None else MISSING
    v["rl_marked"] = str(rl["marked"]) if rl.get("marked") is not None else MISSING

    def short(t: str, n: int = 84) -> str:
        return t if len(t) <= n else t[: n - 2].rstrip() + "…"

    def line(i: dict) -> str:
        cls = "ok" if i["verdict"] == "PROVEN" else "bad"
        ex = f'<span class="ex">[{esc(i["exhibit"])}]</span> ' if i["exhibit"] else ""
        return (f'<div class="rl"><span class="n">{i["n"]:>2}.</span><span class="vd {cls}">{esc(i["verdict"])}</span>'
                f'<span class="tx">{ex}{esc(short(i["text"]))}</span></div>')

    # round 1: the CONTRADICTED line(s) first, then one CLIENT-STATED sentence (not a bare heading ending in **)
    r1_items = r1.get("items", [])
    p1 = [i for i in r1_items if i["verdict"] == "CONTRADICTED"][:2]
    p1 += [i for i in r1_items if i["verdict"] == "CLIENT-STATED ONLY" and not i["text"].endswith("**")][: 3 - len(p1)]
    p1.sort(key=lambda i: i["n"])
    # last round: the same sentences (matched by their opening words), plus anything still flagged
    pl = []
    for i in p1:
        key = i["text"][:40]
        hit = next((j for j in rl.get("items", []) if j["text"].startswith(key)), None)
        if hit and hit not in pl:
            pl.append(hit)
    pl += [j for j in rl.get("items", []) if j["verdict"] != "PROVEN" and j not in pl][: max(0, 3 - len(pl))]
    pl.sort(key=lambda i: i["n"])
    b["receipt_r1"] = "".join(line(i) for i in p1) or MISSING
    b["receipt_rl"] = "".join(line(i) for i in pl) or MISSING
    v["rc_excerpt_n"] = str(len(p1) + len(pl)) if p1 or pl else MISSING

    # --- full LLM-AggreFact run (threshold 0.5) and NDI parsing
    f = load(FULL)
    v["full_claims"] = f"{f['claims_scored']:,}" if num(f.get("claims_scored")) else MISSING
    v["full_requests"] = f"{f['requests']:,}" if num(f.get("requests")) else MISSING
    v["full_cost"] = f"${f['cost_usd']:.2f}" if num(f.get("cost_usd")) else MISSING
    v["full_bacc"] = f"{f['average_balanced_accuracy']:.3f}" if num(f.get("average_balanced_accuracy")) else MISSING
    v["full_p50"] = secs(num(f.get("seconds_per_request_p50")))
    v["full_thr"] = f"{f['threshold']:g}" if num(f.get("threshold")) is not None else MISSING
    v["full_failed"] = str(f["claims_failed"]) if num(f.get("claims_failed")) is not None else MISSING
    v["full_cpr"] = f"{f['claims_per_request_mean']:.2f}" if num(f.get("claims_per_request_mean")) else MISSING
    pds = f.get("per_dataset_balanced_accuracy") or {}
    rows = sorted(((k, x, f"{x:.3f}", False) for k, x in pds.items() if num(x) is not None), key=lambda r: -r[1])
    v["full_n_datasets"] = str(len(rows)) if rows else MISSING
    b["full_bars"] = hbars(rows, width=520, row_h=33, label_w=180, value_w=64)
    nd = load(NDI)
    items = nd.get("items") or []
    try:
        ndi_cost = sum(float(i["cost"]["total_usd"]) for i in items) if items else None
    except (KeyError, TypeError, ValueError):
        ndi_cost = None
    v["ndi_pdfs"] = str(nd["pdfs"]) if num(nd.get("pdfs")) else MISSING
    v["ndi_errors"] = str(len(nd["errors"])) if isinstance(nd.get("errors"), list) else MISSING
    v["ndi_cost"] = f"${ndi_cost:.2f}" if ndi_cost is not None else MISSING
    v["ndi_p50"] = secs(num(nd.get("seconds_p50")))
    cp = load(CASES_PDF).get("drex_rules_fitted", {})
    if num(cp.get("n")) and num(cp.get("accuracy")) is not None:
        v["ndi_drex_correct"] = f"{round(cp['accuracy'] * cp['n'])}/{cp['n']}"
    else:
        v["ndi_drex_correct"] = MISSING

    # --- real USCIS AAO appeal decisions (scripts/aao_scrape.py + aao_label.py -> bench/results/aao_stats.json)
    aao = load(RESULTS / "aao_stats.json")
    sets = [d for d in aao.values() if isinstance(d, dict) and d.get("parsed")]
    if sets:
        n = sum(d["parsed"] for d in sets)
        v["aao_n"] = f"{n:,}"
        v["aao_o1"] = f"{aao.get('aao', {}).get('parsed', 0):,}"
        v["aao_eb1"] = f"{aao.get('aao_19', {}).get('parsed', 0):,}"
        v["aao_cost"] = usd(sum(d["cost_usd"] for d in sets))
        v["aao_model"] = esc(", ".join(sorted({(d.get("model") or "").split("/")[-1] for d in sets})))
        orders = [d["outcome_by_order_line"] for d in sets]
        decided = sum(c.get(k, 0) for c in orders for k in ("dismissed", "sustained", "remanded", "rejected", "withdrawn"))
        dismissed = sum(c.get("dismissed", 0) + c.get("rejected", 0) for c in orders)
        v["aao_dismissed_pct"] = f"{100 * dismissed / decided:.0f}%" if decided else MISSING
        reasons = {}
        for d in sets:
            for k, c in d["main_reason"].items():
                reasons[k] = reasons.get(k, 0) + c
        failed = sum(c for k, c in reasons.items() if k not in ("other", "None", None))
        v["aao_backed_pct"] = f"{100 * reasons.get('claims_not_backed', 0) / failed:.0f}%" if failed else MISSING
        a = [tuple(map(int, d["model_agrees_with_order_line"].split("/"))) for d in sets]
        v["aao_agree"] = f"{sum(x for x, _ in a):,} of {sum(y for _, y in a):,}" if a else MISSING
        names = {"awards": "Awards", "membership": "Memberships", "press": "Press about them", "judging": "Judging others",
                 "contributions": "Original contributions", "articles": "Scholarly articles", "exhibitions": "Exhibitions",
                 "critical_role": "Critical role", "salary": "High salary", "commercial": "Commercial success"}
        nm = {k: sum(d["criteria"].get(k, {}).get("not_met", 0) for d in sets) for k in names}
        rows = sorted(((names[k], c, f"{c:,}", i < 3) for i, (k, c) in enumerate(sorted(nm.items(), key=lambda kv: -kv[1])) if c), key=lambda r: -r[1])
        b["aao_bars"] = hbars(rows, width=900, row_h=min(40, 460 // max(1, len(rows))), label_w=240, value_w=80)
    else:
        for k in ("aao_n", "aao_o1", "aao_eb1", "aao_cost", "aao_model", "aao_dismissed_pct", "aao_backed_pct", "aao_agree"):
            v[k] = MISSING
        b["aao_bars"] = MISSING
    return v, b


def main() -> None:
    vals, blocks = build()
    src = TEMPLATE.read_text()
    unknown = sorted((set(re.findall(r"\{\{(\w+)\}\}", src)) - set(vals)) | (set(re.findall(r"\[\[(\w+)\]\]", src)) - set(blocks)))
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), MISSING), src)
    out = re.sub(r"\[\[(\w+)\]\]", lambda m: blocks.get(m.group(1), MISSING), out)
    OUT.write_text(out)
    missing = sorted(k for k, x in {**vals, **blocks}.items() if x == MISSING)
    print(f"wrote {OUT}: {len(missing)} of {len(vals) + len(blocks)} values missing")
    if unknown:
        print("placeholders with no source:", ", ".join(unknown))
    if "--check" in sys.argv:
        if missing:
            print("missing:", ", ".join(missing))
        for k in sorted(vals):
            print(f"  {k} = {vals[k]}")
    if "--pdf" in sys.argv:
        subprocess.run([CHROME, "--headless=new", "--no-pdf-header-footer", "--virtual-time-budget=8000",
                        f"--print-to-pdf={PDF}", OUT.as_uri()], check=True, capture_output=True)
        print(f"wrote {PDF}")


if __name__ == "__main__":
    main()
