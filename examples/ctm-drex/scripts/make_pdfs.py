"""Render every .md exhibit in a folder to a simple PDF in a sibling folder `exhibits-pdf/`.

    python scripts/make_pdfs.py demo/case-demo/exhibits

Uses reportlab if installed, else macOS `cupsfilter` (text to PDF), else `pandoc`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _reportlab(src: Path, out: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    story = []
    for line in src.read_text().splitlines():
        if not line.strip():
            story.append(Spacer(1, 8))
        elif line.startswith("#"):
            story.append(Paragraph(escape(line.lstrip("# ")), styles["Heading2"]))
        else:
            story.append(Paragraph(escape(line), styles["Normal"]))
    SimpleDocTemplate(str(out), pagesize=letter).build(story)


def _textutil(src: Path, out: Path) -> None:
    """macOS: cupsfilter renders plain text straight to PDF (its HTML filter fails here)."""
    cups = shutil.which("cupsfilter") or "/usr/sbin/cupsfilter"
    with tempfile.TemporaryDirectory() as tmp:
        txt = Path(tmp) / (src.stem + ".txt")
        txt.write_text(src.read_text())
        with out.open("wb") as fh:
            subprocess.run([cups, "-m", "application/pdf", str(txt)], check=True, stdout=fh, stderr=subprocess.DEVNULL)


def _pandoc(src: Path, out: Path) -> None:
    subprocess.run(["pandoc", str(src), "-o", str(out)], check=True, capture_output=True)


def renderer():
    try:
        import reportlab  # noqa: F401

        return _reportlab
    except ImportError:
        pass
    if shutil.which("cupsfilter") or Path("/usr/sbin/cupsfilter").exists():
        return _textutil
    if shutil.which("pandoc"):
        return _pandoc
    raise SystemExit("No PDF renderer found: install reportlab or pandoc")


def main(folder: str) -> None:
    src_dir = Path(folder)
    out_dir = src_dir.parent / "exhibits-pdf"
    out_dir.mkdir(exist_ok=True)
    render = renderer()
    for md in sorted(src_dir.glob("*.md")):
        if md.name.endswith(".ndi.md"):
            continue
        out = out_dir / (md.stem + ".pdf")
        render(md, out)
        print(f"{md.name} -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
