"""Turn every case's exhibits into PDFs and parse each one with NDI, so Drex can check claims against parsed PDFs.

    python scripts/ndi_all.py
Writes data/cases_pdf/<case>/exhibits/<name>.pdf plus NDI's cached <name>.ndi.md, and bench/results/ndi_parse.json
(seconds per page parse, NDI's own reported cost). Run bench with CTM_CASES=cases_pdf to benchmark on the parsed text.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ctm_drex import ndi  # noqa: E402

SRC, DST = ROOT / "data" / "cases", ROOT / "data" / "cases_pdf"


def render() -> list[Path]:
    pdfs = []
    for ex in sorted(SRC.glob("*/exhibits")) + [ROOT / "demo" / "case-demo" / "exhibits"]:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_pdfs.py"), str(ex)], check=True, capture_output=True)
        out = (DST / ex.parent.name / "exhibits") if ex.parent.parent == SRC else ex.parent / "exhibits-pdf"
        out.mkdir(parents=True, exist_ok=True)
        for pdf in sorted((ex.parent / "exhibits-pdf").glob("*.pdf")):
            if out != pdf.parent:
                shutil.copy2(pdf, out / pdf.name)
            pdfs.append(out / pdf.name)
        if out != ex.parent / "exhibits-pdf":
            shutil.rmtree(ex.parent / "exhibits-pdf")
    return pdfs


async def main() -> None:
    pdfs = render()
    sem = asyncio.Semaphore(4)
    log = []

    async def one(pdf: Path):
        async with sem:
            if ndi.cache_path(pdf).exists():
                return
            t = time.perf_counter()
            try:
                text = await ndi.parse_pdf(pdf)
                log.append({"pdf": str(pdf.relative_to(ROOT)), "seconds": time.perf_counter() - t,
                            "chars": len(text), "cost": ndi.last_cost})
            except Exception as e:
                log.append({"pdf": str(pdf.relative_to(ROOT)), "error": str(e)[:200]})

    await asyncio.gather(*(one(p) for p in pdfs))
    ok = [l for l in log if "error" not in l]
    out = {"pdfs": len(pdfs), "parsed_now": len(ok), "errors": [l for l in log if "error" in l],
           "seconds_p50": sorted(l["seconds"] for l in ok)[len(ok) // 2] if ok else None,
           "note": "cost per item is NDI's reported job cost; it can be misattributed under concurrency, sum is approximate",
           "items": log}
    (ROOT / "bench" / "results" / "ndi_parse.json").write_text(json.dumps(out, indent=1))
    print(len(pdfs), "pdfs,", len(ok), "parsed,", len(out["errors"]), "errors, p50", out["seconds_p50"])


asyncio.run(main())
