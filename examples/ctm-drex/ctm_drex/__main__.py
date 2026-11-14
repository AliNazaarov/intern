"""python -m ctm_drex <draft.md> <exhibits-folder> [--json]: check a petition draft and print the receipt."""

import asyncio
import sys
from pathlib import Path

from .check import check_claims, extract_claims, load_exhibits, receipt, to_json
from .drex import Drex


async def main(draft: Path, folder: Path, as_json: bool) -> int:
    claims = extract_claims(draft.read_text())
    exhibits = load_exhibits(folder)
    if not claims or not exhibits:
        print("no claims or no exhibits found", file=sys.stderr)
        return 2
    async with Drex() as drex:
        verdicts, usage = await check_claims(claims, exhibits, drex)
    print(to_json(verdicts, usage) if as_json else receipt(verdicts, usage))
    return 1 if any(v.verdict != "supported" for v in verdicts) else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(Path(args[0]), Path(args[1]), "--json" in sys.argv)))
