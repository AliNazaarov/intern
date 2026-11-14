# CTM × Drex: 2-minute pitch, 2-minute demo, Q&A

## Pitch (2:00), slides 1–6

**1 · Cover (0:00–0:10)**
"I'm Ali, team CTM. Visa packets run to hundreds of pages, and one sentence the evidence doesn't back can sink the case. CTM lets AI agents write the packet and has Drex check every sentence."

**2 · Problem (0:10–0:35)**
"About 16,000 O-1 petitions are decided every year. Roughly one in five gets a Request for Evidence: months of waiting. A third of EB-1A green-card petitions were denied last year. We read all 619 public O-1 appeal decisions: in 63% of failures, the evidence didn't show what the petition claimed. And since August, USCIS can deny without even asking first."

**3 · How it works (0:35–0:60)**
"An agent in Intern drafts the petition. When it says 'done', our finish gate sends every sentence to Drex. Drex checks each one against every exhibit in half a second. Anything flagged goes back to the agent, which fixes only those sentences. The attorney signs a one-page receipt."

**4 · What we use from Nace (1:00–1:15)**
"Six Nace pieces: Drex's multiple-choice questions, probabilities instead of generated text, one warm state for the exhibits, Intern's two systems, Intern extensions for our gate, and NDI to read PDFs."

**5 · How it learns and gets faster (1:15–1:35)**
"We don't fine-tune anything. We calibrate one number, how strict the check is. It's fast because Drex reads the exhibits once and answers 16 questions per pass as probabilities. That's 6× faster than one question at a time, and a whole petition takes half a second."

**6 · Race (1:35–2:00)**
"Same petition, three engines. Drex finishes in half a second, 4× faster and 58× cheaper than GPT-5.6 reading the whole case, with the same accuracy: 151 vs 152 of 153. Vector search is slower and misses proof. Let me show you."

## Demo (2:00)
1. The race page, case **demo-run3**, press Race (about 10 s). Point at the stopwatches, then claim 8: "a team of dozens". Drex says CONTRADICTED, while the other two pass it.
2. Click claim 8 to open the drawer: the press article says dozens, the org chart says 6 people.
3. Pick **case04** and press Race. Click "He is the lead author of that paper." in the vector-search lane: "Vector search never retrieved the paper, so it couldn't see he's third author."
4. If time allows, Upload PDFs: the 6 exhibits plus `petition-draft-round1.md`. NDI parses them, then race again.
5. Optional: play the 75-second Intern recording (Qwen drafts, Drex flags 8, the run reopens, 0 left).

## Q&A prep

**"Is a petition really just 6 exhibits?"**
No. The demo is one section of the petition (Critical Role) with its 6 exhibits. A real packet is the same check repeated: every section, hundreds of claims, dozens of exhibits. At $0.00026 per 15–26 sentences, a whole packet costs cents. Long exhibits get split into several Drex states.

**"Do you train a model?"**
No fine-tuning. We calibrate one number, the strictness threshold, on 50 practice claims. Everything else is Drex as shipped. Next we calibrate per visa criterion on the 1,069 USCIS decisions we collected.

**"Is Drex more accurate than GPT or Claude?"**
Same accuracy class, not more accurate. On 150 public benchmark claims Drex scored 0.752; nine models score up to 0.036 higher, which is within noise. It's tied with Grok 4.7 for letting the fewest bad claims through (14 of 76), and it's 15–34× faster and up to 800× cheaper.

**"Why is it so fast?"**
It writes no text: it returns probabilities for multiple-choice answers in one forward pass. It reads the exhibits once and answers 16 questions per pass. We send batches in parallel.

**"Is it deterministic?"**
Yes. We re-ran 47 claims 5 times and got identical verdicts every time. The probabilities moved by at most 0.012. Code makes the final call with a fixed threshold.

**"Why not just use RAG or a vector database?"**
Vector search finds passages that sound like the claim, and the proof is often in a different document. 8 of its 10 errors were true sentences it flagged because it never retrieved the proving exhibit. It also missed the "team of dozens" contradiction.

**"What about client privacy?"**
Everything here uses synthetic cases and public government decisions. For a firm, the plan is Drex running on-prem, so client files never leave it.

**"Where's the real data?"**
The USCIS appeal decisions are public and redacted, and we committed only counts. Real petitions with their exhibits become public when a denial is challenged in federal court. That's our next benchmark.

**"What if Drex is wrong?"**
The attorney still signs, and the receipt shows the exhibit behind every PROVEN line. A failed check prints NOT CHECKED and never silently passes. The threshold is a dial the firm can set stricter.

**"Users? Revenue?"**
No users yet. Expedite, an AI-native law firm, is the motivating firm, not a user of this build.

**"What's hard / what's next?"**
- Long exhibits beyond one state.
- Scanned handwriting.
- Legal judgement beyond facts, such as "is this award major?" A rules engine for the 8 O-1A criteria is next.
