"""Download USCIS AAO non-precedent decisions on O petitions (I-129, extraordinary ability) and extract their text.

Public pages, no login, one request at a time with a pause, so the USCIS site sees a polite reader.
  uv run --with httpx,pypdf python scripts/aao_scrape.py list      # data/aao/index.jsonl (every listed PDF)
  uv run --with httpx,pypdf python scripts/aao_scrape.py fetch N   # newest N PDFs -> data/aao/pdf + text/
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import httpx

BASE = "https://www.uscis.gov"
LIST = BASE + "/administrative-appeals/aao-decisions/aao-non-precedent-decisions"
import os

# 4 = I-129 O petitions (extraordinary ability, O); 19 = I-140 extraordinary ability (EB-1A), same evidence questions
CATEGORY = int(os.environ.get("AAO_CATEGORY", "4"))
OUT = Path(__file__).resolve().parents[1] / "data" / ("aao" if CATEGORY == 4 else f"aao_{CATEGORY}")
HEADERS = {"User-Agent": "Mozilla/5.0 (research; public AAO decisions)"}
PAUSE = 0.7
_PDF = re.compile(r'href="(/sites/default/files/err/[^"]+?\.pdf)"')
_DATE = re.compile(r"([A-Z]{3})(\d{2})(\d{4})_")
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def listing(client: httpx.Client) -> list[dict]:
    rows, seen = [], set()
    for page in range(0, 400):
        r = client.get(LIST, params={"uri_1": CATEGORY, "m": "All", "y": "All", "items_per_page": 50, "page": page})
        r.raise_for_status()
        links = [l for l in _PDF.findall(r.text) if l not in seen]
        if not links:
            break
        for link in links:
            seen.add(link)
            name = unquote(link.rsplit("/", 1)[1])
            m = _DATE.match(name)
            date = f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{m.group(2)}" if m else None
            rows.append({"id": name.removesuffix(".pdf"), "url": BASE + link, "date": date})
        time.sleep(PAUSE)
    return rows


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    OUT.mkdir(parents=True, exist_ok=True)
    index = OUT / "index.jsonl"
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        if cmd == "list":
            rows = listing(client)
            index.write_text("".join(json.dumps(r) + "\n" for r in rows))
            print(f"{len(rows)} decisions listed -> {index}")
            return
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        rows = sorted((json.loads(l) for l in index.read_text().splitlines()), key=lambda r: r["date"] or "", reverse=True)
        (OUT / "text").mkdir(exist_ok=True)
        done = failed = 0
        for r in rows[:n]:
            txt = OUT / "text" / f"{r['id']}.txt"
            if txt.exists():
                done += 1
                continue
            try:
                resp = client.get(r["url"])
                resp.raise_for_status()
                txt.write_text(pdf_text(resp.content))
                done += 1
            except Exception as e:  # one bad PDF must not stop the run
                failed += 1
                print(f"failed {r['id']}: {e}", file=sys.stderr)
            time.sleep(PAUSE)
        print(f"{done} decisions with text, {failed} failed")


if __name__ == "__main__":
    main()
