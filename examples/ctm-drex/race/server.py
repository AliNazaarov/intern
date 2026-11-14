"""CTM race: check the same petition with Drex, with vector search (RAG), and with a frontier model, live.

Every engine gets the same case (exhibits + claims). The page starts all three at once and shows wall-clock seconds
measured here, dollars from each provider's reported usage (Drex: input tokens x Nace's published price), and how
many verdicts match the hand labels (synthetic cases only; uploaded and demo cases have no labels).

    PYTHONPATH=. ~/.local/share/uv/tools/intern-agent/bin/python race/server.py   # then open http://127.0.0.1:8765

Loopback only; Host and Origin are checked so another site in the browser can't drive the paid APIs, and paid calls
stop once this server process has spent SPEND_CAP_USD.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.openai_compat import check_claims_openai, resolve  # noqa: E402
from bench.run import exhibits_of, load_cases  # noqa: E402
from ctm_drex import ndi  # noqa: E402
from ctm_drex.check import check_claims, extract_claims, load_exhibits  # noqa: E402
from ctm_drex.drex import PRICE_PER_M_INPUT, Drex  # noqa: E402
from race.rag import check_claims_rag  # noqa: E402

HOST, PORT = "127.0.0.1", 8765
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}
BASELINES = {
    "or-gpt-5.6-sol": "GPT-5.6 Sol",
    "di-gemini-3.1-pro": "Gemini 3.1 Pro",
    "di-kimi-k3": "Kimi K3",
    "or-grok-4.7": "Grok 4.7",
}
RAG_JUDGE = "or-gpt-5.6-sol"
THRESHOLD = 0.5  # the petitions default; 0.856 is only fitted for AggreFact
SPEND_CAP_USD = 1.00
MAX_UPLOAD_BYTES = 40_000_000
MAX_EXHIBITS = 20
MAX_CLAIMS = 40
UPLOADS = Path(__file__).parent / "uploads"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}

_spend_lock = threading.Lock()
spent_usd = 0.0
reserved_usd = 0.0
# Held against the cap while a call runs (all three lanes start together); charged in full if the call fails, since
# part of it may have been billed before the error.
ESTIMATE_USD = {"drex": 0.01, "rag": 0.10, "baseline": 0.10, "upload": 0.25}


def _labelled_cases() -> dict[str, dict]:
    return {c: {"rows": [{"claim": r["claim"], "label": r["label"]} for r in rows], "exhibits": exhibits_of(c),
                "title": f"{c} · synthetic, hand-labelled"} for c, rows in load_cases().items()}


_ITEM = re.compile(r"\s*\d+\.\s+(?:PROVEN|CONTRADICTED|CLIENT-STATED ONLY|NOT CHECKED)\s+(?:\[[^\]]+\]\s*)?(.*)")


def _demo_case() -> dict | None:
    """The Intern demo's Round 1 draft (run3): the claims the finish gate checked, before Qwen's rewrite."""
    receipt = ROOT / "demo" / "run3" / "ctm-receipt.md"
    if not receipt.exists():
        return None
    round1 = receipt.read_text().split("Round 2")[0]
    claims = [m.group(1).strip() for line in round1.splitlines() if (m := _ITEM.fullmatch(line))]
    return {"rows": [{"claim": c, "label": None} for c in claims if c],
            "exhibits": load_exhibits(ROOT / "demo" / "run3" / "exhibits"),
            "title": "demo-run3 · Intern draft, round 1 (no hand labels)"}


CASES: dict[str, dict] = _labelled_cases()
if (demo := _demo_case()):
    CASES["demo-run3"] = demo


def case_list() -> list[dict]:
    return [{"id": c, "claims": len(v["rows"]), "exhibits": sorted(v["exhibits"]), "title": v["title"],
             "labelled": any(r["label"] for r in v["rows"]), "claim_texts": [r["claim"] for r in v["rows"]]}
            for c, v in CASES.items()]


def _reserve(est: float) -> bool:
    global reserved_usd
    with _spend_lock:
        if spent_usd + reserved_usd + est > SPEND_CAP_USD:
            return False
        reserved_usd += est
        return True


def _settle(est: float, actual: float) -> None:
    global spent_usd, reserved_usd
    with _spend_lock:
        reserved_usd -= est
        spent_usd += actual or 0.0


async def run_drex(case: str) -> dict:
    c = CASES[case]
    start = time.perf_counter()
    async with Drex() as d:
        verdicts, usage = await check_claims([r["claim"] for r in c["rows"]], c["exhibits"], d, threshold=THRESHOLD)
    seconds = time.perf_counter() - start
    out = [{"claim": v.claim, "verdict": v.verdict, "source": v.source, "note": v.note} for v in verdicts]
    cost = usage["input_tokens"] * PRICE_PER_M_INPUT / 1e6
    return finish(c["rows"], out, seconds, cost, usage["input_tokens"], f"Drex ({usage['model']})", usage["requests"])


async def run_rag(case: str) -> dict:
    c = CASES[case]
    start = time.perf_counter()
    verdicts, usage = await check_claims_rag([r["claim"] for r in c["rows"]], c["exhibits"], judge=RAG_JUDGE, k=3)
    seconds = time.perf_counter() - start
    out = [{"claim": v["claim"], "verdict": v["verdict"], "source": v.get("source"), "note": "",
            "retrieved": v.get("retrieved", [])} for v in verdicts]
    res = finish(c["rows"], out, seconds, usage["cost_usd"], usage["input_tokens"],
                 f"Vector search + {BASELINES[RAG_JUDGE]}", 2)
    res.update(embed_cost_usd=usage["embed_cost_usd"], judge_cost_usd=usage["judge_cost_usd"], chunks=usage["chunks"])
    return res


async def run_baseline(case: str, name: str) -> dict:
    c = CASES[case]
    m = resolve(name)
    start = time.perf_counter()
    verdicts, usage = await check_claims_openai([r["claim"] for r in c["rows"]], c["exhibits"], m["model"],
                                                m["base_url"], m["key"], m.get("extra"), price=m["price"])
    seconds = time.perf_counter() - start
    out = [{"claim": v["claim"], "verdict": v["verdict"], "source": v.get("source"), "note": ""} for v in verdicts]
    return finish(c["rows"], out, seconds, usage["cost_usd"], usage["input_tokens"], BASELINES[name],
                  usage.get("attempts", 1))


def finish(rows: list[dict], out: list[dict], seconds: float, cost: float, tokens: int, label: str, requests: int) -> dict:
    for o, r in zip(out, rows):
        o["expected"] = r["label"]
        o["correct"] = None if r["label"] is None else o["verdict"] == r["label"]
    graded = [o for o in out if o["correct"] is not None]
    return {"engine": label, "seconds": seconds, "cost_usd": cost, "input_tokens": tokens, "requests": requests,
            "correct": sum(o["correct"] for o in graded) if graded else None, "graded": len(graded),
            "claims": len(out), "verdicts": out}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "file").name)[:80] or "file"


async def ingest_upload(files: list[tuple[str, str, bytes]]) -> dict:
    """files: (field, filename, bytes). PDFs under `exhibits` go through NDI in parallel; `draft` may be PDF or text."""
    exhibits = [(f, d) for field, f, d in files if field == "exhibits"]
    drafts = [(f, d) for field, f, d in files if field == "draft"]
    if not exhibits or len(drafts) != 1:
        raise ValueError("send one or more `exhibits` PDFs and exactly one `draft` file")
    if len(exhibits) > MAX_EXHIBITS:
        raise ValueError(f"at most {MAX_EXHIBITS} exhibits")
    for fname, data in exhibits:
        if not data.startswith(b"%PDF"):
            raise ValueError(f"{fname} is not a PDF")
    case = "upload-" + secrets.token_hex(3)
    folder = UPLOADS / case
    folder.mkdir(parents=True)
    paths: list[Path] = []
    for fname, data in exhibits:
        p = folder / _safe_name(fname if fname.lower().endswith(".pdf") else fname + ".pdf")
        p.write_bytes(data)
        paths.append(p)
    dname, ddata = drafts[0]
    costs: list[float] = []
    start = time.perf_counter()
    jobs = [ndi.parse_pdf(p, cost_sink=costs) for p in paths]
    if ddata.startswith(b"%PDF"):
        dpath = folder / ("draft-" + _safe_name(dname))
        dpath.write_bytes(ddata)
        jobs.append(ndi.parse_pdf(dpath, cost_sink=costs))
    texts = await asyncio.gather(*jobs)
    parse_seconds = time.perf_counter() - start
    parse_cost = sum(costs)
    draft = texts[len(paths)] if len(texts) > len(paths) else ddata.decode("utf-8", errors="replace")
    claims = extract_claims(draft)[:MAX_CLAIMS]
    if not claims:
        raise ValueError("no factual sentences found in the draft")
    CASES[case] = {"rows": [{"claim": c, "label": None} for c in claims],
                   "exhibits": {p.name: t for p, t in zip(paths, texts)}, "title": f"{case} · your upload (no hand labels)"}
    return {"case": case, "claims": len(claims), "exhibits": sorted(CASES[case]["exhibits"]), "claim_texts": claims,
            "parse_seconds": parse_seconds, "parse_cost_usd": parse_cost, "title": CASES[case]["title"]}


def parse_multipart(ctype: str, body: bytes) -> list[tuple[str, str, bytes]]:
    msg = BytesParser(policy=email_policy).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise ValueError("expected multipart/form-data")
    out = []
    for part in msg.iter_parts():
        field = part.get_param("name", header="content-disposition")
        if field in ("exhibits", "draft"):
            out.append((field, part.get_filename() or field, part.get_payload(decode=True) or b""))
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline' "
                         "https://fonts.googleapis.com; font-src https://fonts.gstatic.com")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self) -> bool:
        return self.headers.get("Host") in ALLOWED_HOSTS

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "bad host"})
        url = urlsplit(self.path)
        if url.path in STATIC:
            fname, ctype = STATIC[url.path]
            return self._send(200, (Path(__file__).parent / fname).read_bytes(), ctype)
        if url.path == "/api/cases":
            return self._json(200, {"cases": case_list(), "baselines": BASELINES, "spent_usd": spent_usd,
                                    "spend_cap_usd": SPEND_CAP_USD})
        if url.path == "/api/exhibit":
            q = parse_qs(url.query)
            case, name = (q.get("case") or [""])[0], (q.get("name") or [""])[0]
            text = CASES.get(case, {}).get("exhibits", {}).get(name)
            if text is None:
                return self._json(404, {"error": "unknown case or exhibit"})
            return self._json(200, {"name": name, "text": text})
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("Origin") not in ALLOWED_ORIGINS:
            return self._json(403, {"error": "bad host or origin"})
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                raise ValueError("bad Content-Length")
            if path == "/api/check":
                body = json.loads(self.rfile.read(min(length, 10_000)))
                case, engine = body["case"], body["engine"]
                if case not in CASES or (engine not in ("drex", "rag") and engine not in BASELINES):
                    return self._json(400, {"error": "unknown case or engine"})
                est = ESTIMATE_USD["drex" if engine == "drex" else "rag" if engine == "rag" else "baseline"]
                if not _reserve(est):
                    return self._json(429, {"error": f"spend cap of ${SPEND_CAP_USD:.2f} reached; restart the server"})
                run = run_drex(case) if engine == "drex" else run_rag(case) if engine == "rag" else run_baseline(case, engine)
                try:
                    result = asyncio.run(run)
                except BaseException:
                    _settle(est, est)
                    raise
                _settle(est, result["cost_usd"])
                return self._json(200, result)
            if path == "/api/upload":
                if length > MAX_UPLOAD_BYTES:
                    return self._json(413, {"error": "upload too large"})
                files = parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(length))
                if not _reserve(ESTIMATE_USD["upload"]):
                    return self._json(429, {"error": f"spend cap of ${SPEND_CAP_USD:.2f} reached; restart the server"})
                try:
                    result = asyncio.run(ingest_upload(files))
                except BaseException:
                    _settle(ESTIMATE_USD["upload"], ESTIMATE_USD["upload"])
                    raise
                _settle(ESTIMATE_USD["upload"], result["parse_cost_usd"])
                return self._json(200, result)
            return self._json(404, {"error": "not found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)[:300]})
        except Exception as e:  # shown on the page; never includes keys (clients raise status + body excerpt only)
            return self._json(502, {"error": f"{type(e).__name__}: {str(e)[:300]}"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("race: " + fmt % args + "\n")


if __name__ == "__main__":
    print(f"CTM race on http://{HOST}:{PORT} · {len(CASES)} cases · spend cap ${SPEND_CAP_USD:.2f}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
