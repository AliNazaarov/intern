# Drexathon submission: team CTM

**Title:** CTM: the agent team for immigration attorneys, with Drex checking every sentence

**Track:** Apex Predator (Drex vs the Giants)

**GitHub:** https://github.com/AliNazaarov/ctm-drex

## Explanation (for the form)

An O-1A visa packet runs to about 800 pages, and one claim its exhibits don't prove triggers a Request for
Evidence: months of delay. Agents can draft the packet, but they write confident sentences the exhibits don't
back, and checking every sentence with a frontier model is too slow and costly. CTM runs inside Intern: Qwen on
DeepInfra drafts (System 2), Drex is System 1 and the finish gate. NDI parses the PDF exhibits; Drex reads them
once and judges up to 16 questions per pass: proven (with its exhibit), contradicted, or client-stated only.
Flagged claims reopen the run; the attorney signs a one-page receipt. Against 14 models on LLM-AggreFact, Drex is
the cheapest and fastest and ties for fewest bad claims let through.

## Demo screen recording (60 to 90 seconds)

Synthetic demo case (`demo/case-demo`): fictional founder, fictional exhibits.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:08 | `demo/run3/exhibits/`: offer letter, press article, org chart, payroll, email, field report | "A synthetic O-1A case: a founding engineer and the six exhibits that must prove every claim." |
| 0:08–0:25 | Terminal: the `intern -p --system1 drex-latest --system2 deepinfra/Qwen/Qwen3.8-27B --extension ctm_drex.intern_ext:CtmReceipts` run, Qwen reading exhibits and writing the draft | "Intern drafts with Qwen on DeepInfra. Drex is its System 1, and CTM is loaded as the finish gate." |
| 0:25–0:45 | `ctm-receipt.md`, Round 1: 26 claims in 0.52 s, claim 8 CONTRADICTED | "When the agent says it's done, Drex checks all 26 sentences in half a second. The press quote says she built a team of dozens; the org chart says a six-person pod. Contradicted." |
| 0:45–1:00 | Intern reopening the run; Qwen's summary of the fixes | "CTM reopens the run once. The agent removes the overstatement and cites or marks every flagged sentence." |
| 1:00–1:15 | Round 2: 26 claims in 0.45 s, "0 claim(s) need evidence or a rewrite; 1 already marked for the client" | "Round two: nothing left to rewrite, and one item the client must prove. The attorney signs this page instead of reading the case." |

Recording notes: record at 1920×1080, terminal font 18 pt or larger, crop to the window. Run the case once
beforehand so both receipts exist, and cut the model's waiting time rather than speeding it up. State timings
only if they are on screen in the recording.
