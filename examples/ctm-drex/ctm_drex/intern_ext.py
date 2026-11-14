"""CTM as an Intern extension: a finish gate that checks every claim the agent wrote against the exhibits.

When Intern's System 2 says it is done, this extension reads the draft it wrote, has Drex check each factual
sentence against the exhibits in the working directory, writes `ctm-receipt.md`, and, if any claim is
contradicted or client-stated only, reopens the run once with the receipt so the agent fixes those sentences.

    PYTHONPATH=/path/to/ctm-drex intern --extension ctm_drex.intern_ext:CtmReceipts ...

Settings (environment): CTM_DRAFT (default petition-draft.md), CTM_EXHIBITS (default exhibits).
"""

from __future__ import annotations

import os
from pathlib import Path

from intern.hooks import Extension, Nudge, RunContext, RunOutcome

from .check import awaiting_client, check_claims, extract_claims, load_exhibits, receipt
from .drex import Drex


def _not_checked(cwd: Path, why: str) -> None:
    """A stale receipt from an earlier run must never look current."""
    (cwd / "ctm-receipt.md").write_text(f"NOT CHECKED - {why}\n")


class CtmReceipts(Extension):
    name = "ctm-receipts"

    def __init__(self) -> None:
        self.nudged = False
        self.rounds = 0

    async def agent_end(self, outcome: RunOutcome, ctx: RunContext) -> Nudge | None:
        # Only a run the agent itself finished is checked; a stopped or failed run has no final draft.
        cwd = Path(ctx.cwd)
        reason = getattr(outcome.reason, "value", outcome.reason)
        if reason != "completed":
            _not_checked(cwd, f"the run ended with {reason}")
            return None
        try:
            return await self._check(cwd)
        except Exception as e:  # never fail open: an unchecked draft must say so where the attorney looks
            (cwd / "ctm-receipt.md").write_text(
                f"CHECK FAILED - draft not verified\n{type(e).__name__}: {str(e)[:300]}\n"
            )
            return None

    async def _check(self, cwd: Path) -> Nudge | None:
        draft_path = cwd / os.environ.get("CTM_DRAFT", "petition-draft.md")
        exhibits = load_exhibits(cwd / os.environ.get("CTM_EXHIBITS", "exhibits"))
        if not draft_path.exists() or not exhibits:
            _not_checked(cwd, "no draft or no exhibits found")
            return None
        claims = extract_claims(draft_path.read_text())
        if not claims:
            _not_checked(cwd, "no factual sentences found in the draft")
            return None
        async with Drex() as drex:
            verdicts, usage = await check_claims(claims, exhibits, drex)
        self.rounds += 1
        text = receipt(verdicts, usage)
        path = cwd / "ctm-receipt.md"
        history = path.read_text() if self.rounds > 1 and path.exists() else ""
        path.write_text(f"{history}Round {self.rounds}\n{text}\n\n")  # every round kept: what was caught, then fixed
        flagged = [v for v in verdicts if v.verdict != "supported" and not awaiting_client(v)]
        if not flagged or self.nudged:
            return None
        self.nudged = True
        return Nudge(
            text=(
                "CTM checked your draft against the exhibits with Drex before filing:\n\n"
                f"{text}\n\nRewrite every CONTRADICTED sentence to match its exhibit, and either cite an exhibit "
                "for each CLIENT-STATED ONLY sentence or mark it [client to provide evidence]. Then save the draft."
            ),
            reason=f"{len(flagged)} of {len(verdicts)} claims not backed by the exhibits",
        )
