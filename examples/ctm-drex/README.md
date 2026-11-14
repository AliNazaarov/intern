# CTM × Drex: every claim in a visa petition, checked before it's filed

Founders who file for an O-1A visa send a petition letter making dozens of claims ("led a team of 40",
"featured in TechCrunch", "paid in the top 5%") backed by a stack of exhibits. When a sentence isn't backed, or
quietly contradicts its own exhibit, the officer issues a Request for Evidence and the case stalls for months.
AI drafting makes this worse: agents write confident sentences the exhibits don't support.

**CTM is the agent team an immigration attorney runs.** Agents draft the packet; Drex, Nace's decision model, checks
every claim against the exhibits while the draft is being written; the attorney reviews and signs a one-page receipt.
This repository is the checking layer, running inside the Intern harness. Example receipt (illustrative):

```
CTM receipt - 12 claims checked by drex-max-v1.1 in 0.41s
 1. PROVEN             [E6-org-chart.md] Ada is the Founding Engineer and reports to the CTO.
 2. CONTRADICTED       [E6-org-chart.md] Ada leads a team of 40 engineers. (number rule: 40 not stated in E6-org-chart.md)
 3. CLIENT-STATED ONLY  Ada was invited to judge the 2025 RoboCup finals.
...
```

## How it works

- **Read once, ask many.** The case's exhibits are the shared state. One Drex request answers up to 16
  questions about them in a single forward pass: for 8 claims, a verdict (supported / contradicted /
  client-stated only) and which exhibit it rests on. A petition is a few requests in parallel.
- **Code does what decision models are bad at.** A claim Drex passes whose numbers the cited exhibit never
  states is not passed (the number rule).
- **One trained parameter.** The probability of "supported" needed to pass a claim is fitted on a dev split and
  scored on held-out cases.
- **A finish gate inside Intern.** `ctm_drex.intern_ext:CtmReceipts` is an [Intern](https://github.com/nace-ai/intern)
  extension. When the drafting agent (System 2) says it's done, the extension checks its draft, writes
  `ctm-receipt.md`, and reopens the run once if any claim is contradicted or client-stated only.
- **The receipt is the handoff.** The attorney, or the next agent, reads the receipt instead of the whole draft
  and every exhibit.

## Results

Full tables, generated from the raw files: [docs/results.md](docs/results.md). Method and pass criteria:
[docs/evals.md](docs/evals.md). Petition data is synthetic; LLM-AggreFact is Tang, Laban and Durrett's benchmark (2024).

- **LLM-AggreFact, 150 held-out claims, Drex against 14 models** (Claude Opus 5.5 and Haiku 5.5, GPT-5.6 Sol and GPT-5.5,
  Gemini 3.1 Pro and 3.8 Flash, Grok 4.7, Kimi K3, Qwen3.8, DeepSeek V4 Pro, Llama 3.3, Gemma 4, gpt-oss). Drex's balanced
  accuracy is 0.752; nine models score higher, by at most 0.036 (inside the noise at this size). Drex ties Grok 4.7 for
  the fewest unsupported claims passed as supported (14 of 76, with its threshold fitted on dev; 32 at the default 0.5),
  and is the cheapest and fastest model tested: $0.020 per 1,000 claims and 0.27 s per claim, against $3.63 and 4.1 s
  for Grok 4.7 and $16.15 and 4.2 s for Claude Opus 5.5.
- **Full LLM-AggreFact test set, Drex only.** 12,949 claims, average balanced accuracy 0.766 across the 10
  sub-datasets, $0.20 in total.
- **Synthetic O-1A petitions, 106 held-out claims.** Most models score near 1.00 here; Drex checks a petition in
  0.34 s (p50) against 11.4 s for Opus 5.5 and 6.1 s for Gemini 3.1 Pro.
- **Read once, ask many.** Eight claims in one request against one read of the exhibits: 5.5x fewer tokens and 6x
  less time than one request per claim.
- **NDI.** 86 exhibit PDFs parsed by Nace Document Intelligence with no errors; Drex scores the same on the parsed text.

## Run it

Python 3.12+, `httpx`. Keys in the environment or `~/.intern/.env`: `NACE_API_KEY` (Drex), `DEEPINFRA_API_KEY`
(Intern's System 2).

```bash
python -m ctm_drex demo/case-demo/petition-draft.md demo/case-demo/exhibits     # one receipt
python -m bench.run drex && python -m bench.run haiku && python -m bench.run score  # the benchmark
python -m unittest discover -s tests -t .                                          # unit tests

# other baselines: CTM_BASELINE=opus | di-<model> | or-<model> (see bench/openai_compat.py)
CTM_BASELINE=opus python -m bench.run haiku && CTM_BASELINE=opus python -m bench.run score
python scripts/ndi_all.py                      # exhibits as PDFs, parsed by NDI
python -m bench.report                         # regenerate docs/results.md

# Intern drafting with the CTM finish gate (in a copy of the demo case)
cp -R demo/case-demo demo/run1 && cd demo/run1
PYTHONPATH=../.. intern -p --system1 drex-latest --system2 deepinfra/Qwen/Qwen3.8-27B \
  --extension ctm_drex.intern_ext:CtmReceipts "$(cat TASK.md)"
```

### The race page

```bash
PYTHONPATH=. python race/server.py        # http://127.0.0.1:8765 ; ?mock=1 runs without keys
PYTHONPATH=. python race/measure.py       # all cases, three lanes at once -> bench/results/race.json
```

Three lanes check the same claims at the same instant: Drex (all exhibits held as one state), vector search (top-3
chunks per claim from Qwen3-Embedding-8B, judged by GPT-5.6 Sol) and a frontier model reading the whole case. Upload
exhibit PDFs and a draft to race your own case (NDI parses them first). Paid calls stop at $1 per server run.

Claude baselines run through the Claude Code CLI (`claude -p`); open and other models through OpenAI-compatible
APIs (DeepInfra, OpenRouter), with the same prompt and parser.

## Pitch materials

- [Problem one-pager](docs/problem-one-pager.pdf): how many petitions fail and why, with sources.
- [8-slide pitch deck](deck/ctm-pitch-8.pdf) ([HTML](deck/ctm-pitch-8.html)) and the [full 16-slide deck with appendix](deck/ctm-pitch-full.pdf) ([HTML](deck/ctm-pitch-full.html)).
- [2-minute pitch script and Q&A](docs/pitch-script-and-QA.md) and the [demo video shot list](docs/demo-video-recording.md).
- Demo inputs: the six synthetic exhibit PDFs in [demo/run3/exhibits-pdf](demo/run3/exhibits-pdf) and the round-1 draft [demo/run3/petition-draft-round1.md](demo/run3/petition-draft-round1.md), for the race page's Upload button.

## Licence

MIT
