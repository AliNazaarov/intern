# Acceptance evals

All data is synthetic: fictional founders, companies and exhibits written for this benchmark (`data/`). The O-1A
criteria are public law (8 CFR 214.2(o)(3)(iii)). No client data.

## E1. Claim verdicts: Drex vs Claude Haiku 5.5

- **What:** each labelled claim from a petition draft is judged `supported`, `contradicted` or `unsupported`
  (client-stated only) against its case's exhibits.
- **Data:** `data/labels.jsonl`, about 150 claims over 10 cases; cases 01–03 are dev (used only to fit the
  threshold), cases 04–10 are test. Labels were written by a separate agent, not the author of the checker.
- **Systems:** Drex alone (threshold 0.5); Drex plus the number rule; Drex plus the number rule with the
  threshold fitted on dev; Claude Haiku 5.5 through `claude -p`, all claims of a case in one prompt.
- **Metrics (test split):** accuracy, macro-F1, **missed bad claims** (contradicted or unsupported claims passed
  as proven: the filing risk), false alarms, seconds per petition (p50, p95), cost per 1,000 claims.
- **Pass:** Drex (best variant) within 5 points of Haiku on macro-F1, missed bad claims no worse than Haiku,
  and at least 5× faster and 10× cheaper per claim.

## E2. Read once, ask many ("warm telepathy")

- **What:** the same 8 claims against the same exhibits, as one batched request and as 8 single requests; and
  the batched request repeated on the same state.
- **Metric:** seconds and input tokens for each.
- **Pass:** batched is faster than the sum of singles and bills fewer input tokens.

## E3. Handoff size

- **What:** tokens the next agent or the attorney reads: the draft plus exhibits, against the CTM receipt.
- **Metric:** characters ÷ 4 (an estimate), averaged over test cases.

## E4. Finish gate inside Intern

- **What:** Intern (System 2 Qwen3.8-27B on DeepInfra, System 1 Drex) drafts the Critical Role and High
  Remuneration sections for the demo case (`demo/case-demo`), with the `ctm_drex.intern_ext:CtmReceipts`
  extension loaded.
- **Pass:** a receipt is written; any contradicted or client-stated sentence reopens the run once with the
  receipt; the second receipt has fewer flagged claims than the first.

## E5. Units

- `python -m unittest discover -s tests -t .`: claim extraction, number normalisation, the threshold, the number
  rule, batching eight claims per request, the receipt.

## Results

All numbers below were measured in one run each. Raw files: `bench/results/`. The full multi-model tables (11 models), the full
12,949-claim AggreFact run and the NDI run are in [results.md](results.md), generated from the files by `python -m bench.report`.

### E1a. Synthetic O-1A petitions (ours): 106 held-out claims in 7 cases, 47 dev claims in 3 cases

| | Drex (drex-max-v1.1) | Claude Haiku 5.5 |
|---|---|---|
| Accuracy | 1.00 | 0.98 |
| Macro-F1 | 1.00 | 0.98 |
| Missed bad claims (bad claim passed as proven) | 0 of 49 | 0 of 49 |
| False alarms (good claim flagged) | 0 of 57 | 2 of 57 |
| Seconds per petition, p50 / p95 | 0.39 / 0.42 | 11.2 / 12.6 |
| Cost per 1,000 claims | $0.016 | $0.113 |

The threshold fitted on dev was 0.5, the default; the number rule changed no test verdict. **Caveat: this set is too
easy to separate the models.** We wrote it, the bad claims are clear-cut, and both models nearly ace it. It shows the
pipeline works end to end and measures speed and cost on petition-shaped input; it does not prove Drex is more
accurate. Haiku's seconds include `claude -p` start-up on each call. Haiku cost is the CLI's reported cost.

### E1b. LLM-AggreFact (Tang, Laban and Durrett, 2024; their benchmark): 150 held-out claims

| | Drex | Haiku 5.5 |
|---|---|---|
| Balanced accuracy (threshold 0.5) | 0.749 | 0.741 |
| Seconds per claim, p50 / p95 | 0.27 / 0.40 | 3.88 / 5.36 |
| Cost per 1,000 claims | $0.020 | $0.452 |

Stratified sample, 10 sub-datasets x 2 labels, documents under 6,000 characters, seed 7, from a public re-upload of
the test set (the official copy is gated). Each accuracy is about +/-0.07 at 95%, so the two models are tied.

**E1 verdict: pass** on both sets (accuracy within 5 points, no more missed bad claims, >=5x faster, >=10x cheaper on
AggreFact; 7x cheaper on the synthetic set, where Haiku's prompts are short and cached).

### E2. Read once, ask many (case01, 8 claims)

| | Seconds | Input tokens |
|---|---|---|
| 8 claims in one request against one state | 0.38 | 2,730 |
| One request per claim (8 requests, sequential) | 2.26 | 14,924 |

5.5x fewer tokens and 6x less time from asking many questions of one read of the exhibits. **Pass.**

### E3. Handoff size (7 test cases, tokens estimated as characters / 4)

The next agent or attorney reads a receipt of about 464 tokens instead of a draft plus exhibits of about 2,503
tokens: about 5x smaller. **Pass.**

### E5. Units and regressions

17 unit tests pass (`python -m unittest discover -s tests -t .`), including one regression test per review fix:
number units and labels, billion, abbreviations, missing Drex answer marked NOT CHECKED, Intern gate writes
CHECK FAILED on error and skips runs that did not complete.

### Independent review

A separate reviewer (not the author) found 10 issues; all were fixed: number parsing, Haiku failures cached as
verdicts, silent missing Drex answers, claim alignment and split leakage, unfair comparison (Haiku now gets the same
number rule), latency and retry handling, one failure losing a whole run, the Intern gate failing open, claim
extraction, and key parsing. Extractor recall is not yet measured.

## E6. Real USCIS appeal decisions (labelling quality) and E7. determinism

- E6: outcome label vs the decision's own ORDER line (regex, `tests/test_aao.py`). O-1: 582/595 agree; EB-1A: 450/450.
  Caveat: criterion labels and main reason are model labels (Gemma 4 31B), not checked by hand.
- E7: 47 synthetic claims × 5 runs: identical verdicts and sources; max p(supported) difference 0.012.

## E8. The three-lane race (race/server.py, race/measure.py)

- What: the same claims checked by Drex, vector search (RAG: ~400-char chunks, Qwen3-Embedding-8B, top-3 per claim,
  GPT-5.6 Sol judge) and GPT-5.6 Sol reading the whole case, started together. Synthetic data: the 10 labelled cases
  (153 claims) and the Intern demo draft (run3 round 1, 26 claims, no hand labels).
- Expected: Drex at least as accurate as RAG, within noise of the frontier lane, faster and cheaper than both.
- Result (one run each, 14 Nov, `bench/results/race.json`): Drex 151/153 correct, p50 0.53 s, $0.00026 per petition;
  GPT-5.6 Sol 152/153, 2.29 s, $0.015; RAG 143/153, 3.46 s, $0.020. 8 of RAG's 10 misses are supported claims marked
  unsupported because the proving exhibit was not retrieved. On the demo draft's "team of dozens" claim only Drex
  answered contradicted (org chart: 6-person pod); there is no hand label for it.
- Caveats: one run; live latency varies (single page races ranged 0.5-0.7 s Drex, 2-4 s GPT-5.6 Sol, 3-12 s RAG); the
  petition set is easy (frontier models score ~1.0), so it shows speed, cost and RAG's retrieval misses, not a ranking.
- Tests: `tests/test_race_server.py` (Host/Origin, exhibit lookup, spend cap, multipart, file names),
  `tests/test_rag.py` (chunking, top-k, prompt layout).
- Live cost also varies: repeat races of the same case cost GPT-5.6 Sol about $0.007 instead of $0.015, likely
  provider-side prompt caching. The page shows each run's own reported cost.
- Independent review (separate agent): no security findings. Fixed, each with a regression test in
  `tests/test_race_server.py`: frontier request count now reports real attempts; spend is reserved before a call (three
  lanes can no longer overrun the cap together) and a failed call is charged its estimate; upload parse cost is summed
  per upload, not from a shared log; negative Content-Length returns 400. In `race/app.js`: no speed/cost ratio when a
  lane returned only errors; `?mock=1` results are captioned "MOCK DATA" (no JS test harness; checked by hand).
