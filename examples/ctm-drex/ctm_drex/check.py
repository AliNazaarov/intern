"""Check every claim in a petition draft against the case's exhibits, with Drex making each call.

One Drex request carries the exhibits as its state and up to eight claims, two questions each:
  verdict  - supported / contradicted / unsupported (client-stated only)
  source   - which exhibit backs or contradicts it, or none
A whole petition is a handful of requests sent in parallel.

Code, not the model, handles what decision models are weak at: a claim Drex calls supported whose numbers
do not appear in the exhibit it cites is not passed; it falls to Drex's next most likely verdict.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .drex import Choice, Drex

LABELS = ("supported", "contradicted", "unsupported")
VERDICT_OPTIONS = {
    "supported": "An exhibit clearly states this; every name, number, title and date in the claim matches the exhibit.",
    "contradicted": (
        "Any exhibit says something incompatible: a different number, title, date, rank, outcome or role. This holds "
        "even if another exhibit (for example a press quote) repeats the claim: records outweigh hearsay."
    ),
    "unsupported": "No exhibit addresses this claim; it rests only on what the client said.",
}
CLAIMS_PER_ASK = 8  # two questions per claim, 16 questions per request at most
# Below this probability of "supported", a claim does not pass. bench/run.py fits a value on the dev split.
DEFAULT_THRESHOLD = 0.5


@dataclass
class Verdict:
    claim: str
    verdict: str
    probabilities: dict[str, float]
    source: str | None
    note: str = ""


def load_exhibits(folder: Path) -> dict[str, str]:
    """{filename: text} for .md, .txt and .pdf exhibits. PDFs go through NDI (or its `<name>.ndi.md` cache)."""
    out: dict[str, str] = {}
    for p in sorted(Path(folder).glob("*")):
        if not p.is_file() or p.name.endswith(".ndi.md"):
            continue  # NDI cache files belong to their PDF
        if p.suffix in {".md", ".txt"}:
            out[p.name] = p.read_text()
        elif p.suffix.lower() == ".pdf":
            from .ndi import parse_pdf_sync  # imported only when a PDF needs it

            out[p.name] = parse_pdf_sync(p)
    return out


def exhibits_state(exhibits: dict[str, str]) -> str:
    parts = [f"=== EXHIBIT {name} ===\n{text.strip()}" for name, text in exhibits.items()]
    return "Exhibits filed on an O-1A visa case. They are the only evidence.\n\n" + "\n\n".join(parts)


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_FACT = re.compile(r"\d|\b(led|founded|award|won|published|member|judge|featured|salary|paid|role|title|cto|ceo|head)\b", re.I)
_PROPER = re.compile(r"\s[A-Z][a-z]+")  # a capitalised word after the first: a name, company or title
_ABBREV = re.compile(r"\b(Dr|Mr|Mrs|Ms|Prof|Inc|Ltd|Co|Corp|Jr|Sr|St|U\.S|e\.g|i\.e|vs|No)\.")
_DOT = "\u2024"  # one-dot leader stands in for an abbreviation's dot while splitting


def extract_claims(draft: str) -> list[str]:
    """Factual sentences of a draft: headings, bullets markup and short connective sentences are skipped."""
    claims: list[str] = []
    for block in draft.split("\n"):
        block = block.strip().lstrip("-*• ").strip()
        if not block or block.startswith("#") or len(block) < 25:
            continue
        protected = _ABBREV.sub(lambda m: m.group(0)[:-1] + _DOT, block)
        for s in _SENT.split(protected):
            s = s.strip().replace(_DOT, ".")
            if len(s) >= 25 and (_FACT.search(s) or _PROPER.search(s)):
                claims.append(s)
    return claims


# A number not glued to letters ("E3", "O-1A" are labels, not quantities); unit words need a word boundary, and
# the one-letter units k/m must touch the digits ("$182k", "5m"), so "14 members" stays 14.
_NUM = re.compile(
    r"(?<![A-Za-z\d_.,])(?<![A-Za-z]-)\$?\d[\d,]*(?:\.\d+)?(?:\s*(?:million|thousand|billion|percent)\b|%|[km]\b)?", re.I
)


_CITE = re.compile(r"(?:\d+\s*)?(?:§+|C\.F\.R\.|U\.S\.C\.|CFR|USC)\s*§*\s*[\d.()a-zA-Z]+")  # legal citations are not facts


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    text = _CITE.sub(" ", text)
    for m in _NUM.finditer(text):
        raw = m.group(0).lower().replace("$", "").replace(",", "").strip()
        mult = 1.0
        for suffix, factor in (("million", 1e6), ("billion", 1e9), ("thousand", 1e3), ("percent", 1), ("%", 1), ("k", 1e3), ("m", 1e6)):
            if raw.endswith(suffix):
                raw, mult = raw[: -len(suffix)].strip(), factor
                break
        try:
            out.add(float(raw) * mult)
        except ValueError:
            continue
    return out


def numbers_missing(claim: str, exhibit_text: str) -> set[float]:
    """Numbers in the claim that the exhibit never states (years and amounts alike)."""
    return _numbers(claim) - _numbers(exhibit_text)


def _cited_text(claim: str, source: str | None, exhibits: dict[str, str]) -> str:
    """Text of every exhibit the sentence cites by file name, plus the one Drex picked; all exhibits if none."""
    names = {n for n in exhibits if n in claim or Path(n).stem in claim}
    if source:
        names.add(source)
    return "\n".join(exhibits[n] for n in names) if names else "\n".join(exhibits.values())


MARKED = "[client to provide evidence"


def awaiting_client(v: "Verdict") -> bool:
    """Marked for the client and only unsupported: a contradicted sentence is never excused by the marker."""
    return v.verdict == "unsupported" and MARKED in v.claim


def decide(probs: dict[str, float], threshold: float) -> str:
    if probs.get("supported", 0.0) >= threshold:
        return "supported"
    rest = {k: v for k, v in probs.items() if k != "supported"}
    return max(rest, key=rest.get) if rest else "unsupported"


async def check_claims(
    claims: list[str],
    exhibits: dict[str, str],
    drex: Drex,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    number_rule: bool = True,
) -> tuple[list[Verdict], dict]:
    state = exhibits_state(exhibits)
    source_options = {name: f"Exhibit {name}" for name in exhibits} | {"none": "No exhibit addresses this claim."}
    batches = [claims[i : i + CLAIMS_PER_ASK] for i in range(0, len(claims), CLAIMS_PER_ASK)]

    async def one(batch: list[str]):
        questions: dict[str, Choice] = {}
        for i, claim in enumerate(batch):
            questions[f"v{i}"] = Choice(
                f"A paralegal's petition draft makes this claim about the beneficiary:\n\"{claim}\"\n"
                "Judged only against the exhibits, is the claim supported, contradicted, or unsupported?",
                VERDICT_OPTIONS,
            )
            questions[f"s{i}"] = Choice(
                f"Which exhibit is the evidence for or against this claim:\n\"{claim}\"", source_options
            )
        return batch, await drex.ask(state, questions)

    results = await asyncio.gather(*(one(b) for b in batches))
    verdicts: list[Verdict] = []
    tokens, seconds = 0, 0.0
    for batch, res in results:
        tokens += res.input_tokens
        seconds = max(seconds, res.seconds)
        for i, claim in enumerate(batch):
            v = res.answers.get(f"v{i}", {})
            probs = {k: float(p) for k, p in (v.get("probabilities") or {}).items() if k in LABELS}
            if not probs and v.get("choice") in LABELS:
                probs = {v["choice"]: 1.0}
            if not probs:  # Drex gave no usable answer: never pass or silently fail a claim on that
                verdicts.append(Verdict(claim, "error", {}, None, "Drex returned no answer for this claim"))
                continue
            s = res.answers.get(f"s{i}", {}).get("choice")
            source = s if s in exhibits else None
            verdict = decide(probs, threshold)
            note = ""
            if number_rule and verdict == "supported":
                cited = _cited_text(claim, source, exhibits)
                missing = numbers_missing(claim, cited)
                if missing:
                    rest = {k: p for k, p in probs.items() if k != "supported"}
                    verdict = max(rest, key=rest.get) if rest else "unsupported"
                    shown = ", ".join(f"{n:g}" for n in sorted(missing))
                    note = f"number rule: {shown} not stated in {source or 'any exhibit'}"
            verdicts.append(Verdict(claim, verdict, probs, source, note))
    usage = {"input_tokens": tokens, "seconds": seconds, "requests": len(batches), "model": drex.model}
    return verdicts, usage


def receipt(verdicts: list[Verdict], usage: dict) -> str:
    """A plain-text receipt an attorney (or the next agent) reads instead of the whole draft and exhibits."""
    mark = {"supported": "PROVEN", "contradicted": "CONTRADICTED", "unsupported": "CLIENT-STATED ONLY", "error": "NOT CHECKED"}
    lines = [f"CTM receipt - {len(verdicts)} claims checked by {usage['model']} in {usage['seconds']:.2f}s"]
    for n, v in enumerate(verdicts, 1):
        src = f" [{v.source}]" if v.source else ""
        extra = f" ({v.note})" if v.note else ""
        lines.append(f"{n:>2}. {mark[v.verdict]:<18}{src} {v.claim}{extra}")
    marked = sum(awaiting_client(v) for v in verdicts)
    bad = sum(v.verdict != "supported" for v in verdicts) - marked
    lines.append(f"{bad} claim(s) need evidence or a rewrite before filing; {marked} already marked for the client.")
    return "\n".join(lines)


def to_json(verdicts: list[Verdict], usage: dict) -> str:
    return json.dumps({"usage": usage, "verdicts": [asdict(v) for v in verdicts]}, indent=2)
