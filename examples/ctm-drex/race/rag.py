"""The third race lane: classic RAG. Chunk the exhibits, embed chunks and claims, retrieve top-k chunks per claim
by cosine similarity, and let a judge model decide each claim from ONLY its retrieved chunks.

Same verdict options (ctm_drex.check.VERDICT_OPTIONS) and the same JSON answer style as bench/haiku.py, parsed by
bench.haiku.parse_verdicts. All claims of a case go to the judge in ONE chat call, each claim followed by its own
retrieved chunks; the embedding is ONE batched call (chunks and claims together).

Embedding cost is DeepInfra's usage.estimated_cost when returned, else tokens x the published price ($0.01/M input
tokens for Qwen3-Embedding-8B, GET /v1/openai/models on 2026-11-14); `embed_price_basis` says which.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

import httpx

EMBED_BASE = "https://api.deepinfra.com/v1/openai"
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBED_PRICE_PER_M = 0.01
# Qwen3-Embedding is instruction-aware: queries get a task line, documents do not.
QUERY_INSTRUCT = "Instruct: Given a claim from a visa petition, retrieve exhibit passages that support or contradict it\nQuery: "

CHUNK_CHARS = 400
OVERLAP_CHARS = 80

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

PROMPT = """You check an O-1A visa petition draft against its exhibits before filing.
For each numbered claim, decide one verdict:
{options}
and name the exhibit file that is the evidence for or against it, or "none".

You do not see the whole case. Under each claim are the exhibit passages retrieved for it, labelled with their
exhibit file. Judge each claim only from the passages under it.

{blocks}

Reply with JSON only, no prose: [{{"i": 1, "verdict": "supported|contradicted|unsupported", "source": "<file or none>"}}, ...]"""


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """~size-character chunks built from whole sentences (paragraph breaks also split). The last sentences of a
    chunk, up to `overlap` characters, start the next one. A single sentence longer than size is cut by characters."""
    pieces: list[str] = []
    for s in SENTENCE.split(text or ""):
        s = s.strip()
        while len(s) > size:
            pieces.append(s[:size])
            s = s[size - overlap:]
        if s:
            pieces.append(s)
    chunks: list[str] = []
    cur: list[str] = []
    for p in pieces:
        if cur and len(" ".join(cur + [p])) > size:
            chunks.append(" ".join(cur))
            tail: list[str] = []
            for q in reversed(cur):  # carry whole trailing sentences that fit in the overlap
                if len(" ".join([q] + tail)) > overlap:
                    break
                tail.insert(0, q)
            cur = tail
        cur.append(p)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_exhibits(exhibits: dict[str, str], size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    return [{"exhibit": name, "text": c} for name, text in sorted(exhibits.items()) for c in chunk_text(text, size, overlap)]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def top_k(query: list[float], vectors: list[list[float]], k: int) -> list[tuple[int, float]]:
    """Indices and cosine scores of the k most similar vectors, best first (ties keep the lower index first)."""
    scored = sorted(((i, cosine(query, v)) for i, v in enumerate(vectors)), key=lambda t: (-t[1], t[0]))
    return scored[:k]


async def embed(texts: list[str], key: str, model: str = EMBED_MODEL, client: httpx.AsyncClient | None = None,
                tries: int = 3) -> tuple[list[list[float]], dict]:
    """One batched embeddings call. Returns vectors in input order and {tokens, cost_usd, price_basis}."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
    try:
        last: Exception | None = None
        for attempt in range(tries):
            try:
                r = await client.post(f"{EMBED_BASE}/embeddings", headers={"Authorization": f"Bearer {key}"},
                                      json={"model": model, "input": texts, "encoding_format": "float"})
                if r.status_code >= 400 and r.status_code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"{model} refused the embeddings request: HTTP {r.status_code} {r.text[:200]}")
                r.raise_for_status()
                body = r.json()
                break
            except httpx.HTTPError as e:
                last = e
                if attempt == tries - 1:
                    raise RuntimeError(f"{model}: embeddings failed after {tries} tries: {str(last)[:200]}")
                await asyncio.sleep(2 * 2 ** attempt)
    finally:
        if own:
            await client.aclose()
    rows = sorted(body["data"], key=lambda d: d["index"])
    u = body.get("usage") or {}
    tokens = int(u.get("prompt_tokens") or u.get("total_tokens") or 0)
    if u.get("estimated_cost") is not None:
        cost, basis = float(u["estimated_cost"]), "provider usage.estimated_cost"
    else:
        cost, basis = tokens * EMBED_PRICE_PER_M / 1e6, f"published price ${EMBED_PRICE_PER_M}/M input tokens"
    return [d["embedding"] for d in rows], {"tokens": tokens, "cost_usd": cost, "price_basis": basis}


def build_rag_prompt(claims: list[str], retrieved: list[list[dict]]) -> str:
    from ctm_drex.check import VERDICT_OPTIONS

    options = "\n".join(f"- {k}: {v}" for k, v in VERDICT_OPTIONS.items())
    blocks = []
    for i, (claim, hits) in enumerate(zip(claims, retrieved), 1):
        passages = "\n".join(f"  [{h['exhibit']}] {h['text']}" for h in hits) or "  (no passages)"
        blocks.append(f"{i}. {claim}\n Retrieved passages:\n{passages}")
    return PROMPT.format(options=options, blocks="\n\n".join(blocks))


async def check_claims_rag(claims: list[str], exhibits: dict[str, str], judge: str = "or-gpt-5.6-sol",
                           k: int = 3) -> tuple[list[dict], dict]:
    from bench.haiku import SYSTEM, parse_verdicts
    from bench.openai_compat import chat, key_for, resolve

    start = time.perf_counter()
    chunks = chunk_exhibits(exhibits)
    vectors, emb = await embed([c["text"] for c in chunks] + [QUERY_INSTRUCT + c for c in claims],
                               key_for("DEEPINFRA_API_KEY"))
    chunk_vecs, claim_vecs = vectors[:len(chunks)], vectors[len(chunks):]
    retrieved = [[{**chunks[i], "score": round(s, 4)} for i, s in top_k(q, chunk_vecs, k)] for q in claim_vecs]

    entry = resolve(judge)
    text, usage = await chat(SYSTEM, build_rag_prompt(claims, retrieved), entry["model"], entry["base_url"],
                             entry["key"], entry.get("extra"), price=entry.get("price"))
    verdicts = parse_verdicts(text, claims)
    for v, hits in zip(verdicts, retrieved):
        v["retrieved"] = hits
    return verdicts, {
        "seconds": time.perf_counter() - start,
        "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
        "cost_usd": emb["cost_usd"] + usage["cost_usd"],
        "embed_cost_usd": emb["cost_usd"], "judge_cost_usd": usage["cost_usd"],
        "embed_model": EMBED_MODEL, "model": usage.get("model") or entry["model"],
        "embed_tokens": emb["tokens"], "embed_price_basis": emb["price_basis"],
        "judge_price_basis": usage.get("price_basis"), "chunks": len(chunks),
    }
