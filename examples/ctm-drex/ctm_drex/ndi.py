"""Nace Document Intelligence (NDI): turn PDF exhibits into Markdown, and find where a quote sits in one.

Endpoints (https://console.nace.ai/docs/perception, same key and balance as Drex):
  POST /v1/documents/upload-grants          -> {workspace_id, upload_url, token}
  POST {upload_url}  (X-Upload-Token, multipart: file + metadata JSON text field)
                                             -> job, result.file.{workspace_id, file_id}
  POST /v1/documents/parse?wait_seconds=60   {source: workspace_file, output: {formats: [markdown]}}
  POST /v1/documents/ground?wait_seconds=60  {source, targets: [{id, text}]}
  GET  /v1/documents/jobs/{id}               poll until succeeded / failed / cancelled
Every job carries `cost` (Drex wallet charge, cost.total_usd) once settled.

Parsed text is cached next to the PDF as `<name>.ndi.md`, so a re-run never re-bills. The upload's
workspace file is cached as `<name>.ndi.json` so ground() can reuse it without uploading again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

from .drex import CONSOLE_URL, _key

TERMINAL = {"succeeded", "failed", "cancelled"}
PARSE_MODE = os.environ.get("CTM_NDI_MODE", "low")  # low = native OCR, the cheapest setting
POLL_TIMEOUT = float(os.environ.get("CTM_NDI_TIMEOUT", "300"))

# The last job's charge and timing, for receipts and the demo.
last_cost: dict | None = None
last_timing: dict | None = None


class NDIError(RuntimeError):
    pass


def cache_path(pdf: Path) -> Path:
    return pdf.with_name(pdf.stem + ".ndi.md")


def _ref_path(pdf: Path) -> Path:
    return pdf.with_name(pdf.stem + ".ndi.json")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_key()}"}


async def _upload(client: httpx.AsyncClient, pdf: Path) -> dict:
    """Upload once; reuse the workspace file while the PDF's bytes are unchanged."""
    data = pdf.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    ref = _ref_path(pdf)
    if ref.exists():
        try:
            saved = json.loads(ref.read_text())
            if saved.get("sha256") == digest:
                return {"type": "workspace_file", "workspace_id": saved["workspace_id"], "file_id": saved["file_id"]}
        except (ValueError, KeyError):
            pass
    # Path keyed by content so a changed file never collides with an old upload at the same name.
    path = f"ctm-drex/{digest[:16]}/{pdf.name}"
    r = await client.post(f"{CONSOLE_URL}/documents/upload-grants", headers=_headers(), json={"path": path, "ttl_seconds": 600})
    _raise(r, "upload grant")
    grant = r.json()
    r = await client.post(
        grant["upload_url"],
        headers={"X-Upload-Token": grant["token"]},
        files={"file": (pdf.name, data, "application/pdf")},
        data={"metadata": json.dumps({"path": path})},
    )
    _raise(r, "upload")
    body = r.json()
    f = (body.get("result") or {}).get("file") or {}
    if not f.get("file_id"):
        body = await _wait(client, body)
        f = body["result"]["file"]
    source = {"type": "workspace_file", "workspace_id": f.get("workspace_id", grant["workspace_id"]), "file_id": f["file_id"]}
    ref.write_text(json.dumps({"sha256": digest, "workspace_id": source["workspace_id"], "file_id": source["file_id"]}))
    return source


def _raise(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        try:
            err = r.json().get("error", r.text[:200])
        except ValueError:
            err = r.text[:200]
        raise NDIError(f"NDI {what} failed: HTTP {r.status_code} {err}")


async def _wait(client: httpx.AsyncClient, job: dict) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT
    delay = 1.0
    while job.get("status") not in TERMINAL:
        if time.monotonic() > deadline:
            raise NDIError(f"NDI job {job.get('job_id')} did not finish in {POLL_TIMEOUT:.0f}s")
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 10.0)
        r = await client.get(f"{CONSOLE_URL}/documents/jobs/{job['job_id']}", headers=_headers())
        _raise(r, "job read")
        job = r.json()
    if job["status"] != "succeeded":
        raise NDIError(f"NDI job {job.get('job_id')} {job['status']}: {job.get('error')}")
    return job


async def _run(client: httpx.AsyncClient, tool: str, body: dict, cost_sink: list[float] | None = None) -> dict:
    global last_cost, last_timing
    r = await client.post(f"{CONSOLE_URL}/documents/{tool}", params={"wait_seconds": 60}, headers=_headers(), json=body)
    _raise(r, tool)
    job = await _wait(client, r.json())
    last_cost, last_timing = job.get("cost"), job.get("timing")
    if cost_sink is not None and last_cost and last_cost.get("total_usd") is not None:
        cost_sink.append(float(last_cost["total_usd"]))
    return job


async def parse_pdf(path: str | Path, *, refresh: bool = False, cost_sink: list[float] | None = None) -> str:
    """Markdown of the document; read from `<name>.ndi.md` when it is there."""
    pdf = Path(path)
    cache = cache_path(pdf)
    if cache.exists() and not refresh:
        return cache.read_text()
    async with httpx.AsyncClient(timeout=120) as client:
        source = await _upload(client, pdf)
        job = await _run(client, "parse", {"source": source, "parse_mode": PARSE_MODE, "output": {"formats": ["markdown"]}},
                         cost_sink)
    doc = (job.get("result") or {}).get("document") or {}
    text = doc.get("markdown") or doc.get("text") or ""
    if not text:
        raise NDIError(f"NDI parse of {pdf.name} returned no text")
    cache.write_text(text)
    return text


async def ground(path: str | Path, quote: str) -> dict | None:
    """Where `quote` appears in the PDF: the best match ({page, matched_text, location, ...}) or None."""
    async with httpx.AsyncClient(timeout=120) as client:
        source = await _upload(client, Path(path))
        job = await _run(client, "ground", {"source": source, "targets": [{"id": "q", "text": quote}], "options": {"max_matches": 1}})
    for target in (job.get("result") or {}).get("targets", []):
        if target.get("status") == "found" and target.get("matches"):
            m = target["matches"][0]
            loc = m.get("location") or {}
            return {"page": loc.get("page"), "matched_text": m.get("matched_text"), "match_method": m.get("match_method"), "location": loc}
    return None


def parse_pdf_sync(path: str | Path) -> str:
    """parse_pdf for sync callers; works inside a running event loop too (runs in a worker thread)."""
    pdf = Path(path)
    if cache_path(pdf).exists():
        return cache_path(pdf).read_text()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(parse_pdf(pdf))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, parse_pdf(pdf)).result()
