"""A small client for Drex on the Nace console: POST /v1/systemone with a state and typed questions.

Drex returns a probability for every option in one forward pass and writes no text. We only ever ask it
choice questions here, so every answer is a label from a fixed set plus its probabilities.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

CONSOLE_URL = os.environ.get("NACE_BASE_URL", "https://console.nace.ai/v1")
DEFAULT_MODEL = os.environ.get("CTM_DREX_MODEL", "drex-latest")
# Nace's published price for Drex Max 1.1 (launch discount): $0.03 per million input tokens, output free.
PRICE_PER_M_INPUT = float(os.environ.get("CTM_DREX_PRICE_PER_M", "0.03"))
MAX_QUESTIONS = 16  # per request; the API refuses more
RETRYABLE = (429, 500, 502, 503, 504, 529)
if not (CONSOLE_URL.startswith("https://") or CONSOLE_URL.startswith(("http://127.0.0.1", "http://localhost"))):
    raise RuntimeError("NACE_BASE_URL must be https (or localhost): the API key is sent with every request")


def _key() -> str:
    key = os.environ.get("NACE_API_KEY")
    if not key:
        env = Path.home() / ".intern" / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                name, _, value = line.partition("=")
                if name.strip().removeprefix("export ").strip() == "NACE_API_KEY" and value.strip():
                    key = value.strip().strip("'\"")
    if not key:
        raise RuntimeError("NACE_API_KEY is not set (environment or ~/.intern/.env)")
    return key


@dataclass
class Choice:
    """One choice question: instructions plus option name -> what the option means."""

    instructions: str
    options: dict[str, str]


@dataclass
class Result:
    answers: dict[str, dict]
    input_tokens: int
    seconds: float
    model: str
    cost_usd: float = field(init=False)

    def __post_init__(self) -> None:
        self.cost_usd = self.input_tokens * PRICE_PER_M_INPUT / 1_000_000


class Drex:
    def __init__(self, model: str = DEFAULT_MODEL, concurrency: int = 16) -> None:
        self.model = model
        self._sem = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Drex:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def ask(self, state: str, questions: dict[str, Choice]) -> Result:
        """Ask up to MAX_QUESTIONS choice questions about one state. Retries 429/5xx twice."""
        if len(questions) > MAX_QUESTIONS:
            raise ValueError(f"at most {MAX_QUESTIONS} questions per ask")
        assert self._client is not None, "use `async with Drex()`"
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                name: {"type": "choice", "instructions": q.instructions, "criteria": q.options}
                for name, q in questions.items()
            },
        }
        headers = {"Authorization": f"Bearer {_key()}"}
        async with self._sem:
            last: Exception | None = None
            start = time.perf_counter()  # what the caller waits, retries and backoff included
            for attempt in range(3):
                try:
                    r = await self._client.post(f"{CONSOLE_URL}/systemone", json=payload, headers=headers)
                    if r.status_code in RETRYABLE:
                        raise httpx.HTTPStatusError(str(r.status_code), request=r.request, response=r)
                    if r.status_code >= 400:  # 400/401/403/422: retrying will not help
                        raise RuntimeError(f"Drex refused the request: HTTP {r.status_code} {r.text[:200]}")
                    body = r.json()
                    return Result(
                        answers=body["answers"],
                        input_tokens=int((body.get("usage") or {}).get("input_tokens") or 0),
                        seconds=time.perf_counter() - start,
                        model=body.get("model", self.model),
                    )
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    last = e
                    if attempt < 2:
                        await asyncio.sleep(1.5 * 2**attempt)
            raise RuntimeError(f"Drex failed after retries: {last}")
