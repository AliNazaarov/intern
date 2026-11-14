> Earlier 5-minute version. The current 2-minute script is [pitch-script-and-QA.md](pitch-script-and-QA.md).

# Pitch: 5 minutes, Apex Predator track

1. **The problem (30 s).** A founder's O-1A petition makes ~40 claims backed by ~30 exhibits. One claim that
   isn't backed, or contradicts its exhibit, and the officer sends a Request for Evidence: months of delay.
   AI drafting agents write confident sentences their exhibits don't support. Expedite, an AI-native startup
   law firm, lives this every day.
2. **The giant today (20 s).** Teams ask a frontier model to re-read the whole case and judge each claim: slow,
   costly, so it runs once, at the end, by hand.
3. **Our move (40 s).** Give the decisions to Drex. The exhibits are one shared state; Drex answers 16 questions
   about them in one forward pass. Code handles numbers. One threshold trained on a dev split.
4. **Demo (90 s, recording).** Intern drafts the Critical Role section with Qwen3.8-27B; it overstates the team
   size and salary; CTM's finish gate, Drex inside Intern, flags both in under a second and reopens the run; the
   second draft passes. The attorney reads a 15-line receipt.
5. **Numbers (60 s).** Drex vs Claude Haiku 5.5 on held-out synthetic cases: accuracy, missed bad claims, seconds
   per petition, cost per 1,000 claims. Read-once-ask-many: batched vs one-per-request. Handoff tokens: receipt vs
   draft plus exhibits.
6. **Why it matters + next (30 s).** Check every sentence as it's written, not once at the end. Next: open Drex
   weights on-prem with a warm case cache, so client files never leave the firm; NDI to ground claims in scanned
   exhibits.

Stack: Drex (System 1 and the checker), Intern (harness and finish gate), DeepInfra (System 2, partner),
Claude Haiku 5.5 (baseline only).
