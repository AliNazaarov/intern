# Recording the demo video (about 2 minutes)

## Setup (5 min)
1. Start the race server: from the repo root, `PYTHONPATH=. python race/server.py` (see the README section "The race page")
2. Open http://127.0.0.1:8765 in Chrome. Full screen (Ctrl+Cmd+F), zoom 110–125% so text reads on video.
3. Close Slack, mail and notifications (Focus mode on). Hide bookmarks bar.
4. Do one practice race first so NDI and Drex are warm.
5. Record with **Cmd+Shift+5 → Record Entire Screen**, Options → Microphone: your mic. (QuickTime → New Screen Recording works the same way.) Stop from the menu-bar button.

## Shot list
| Time | Screen | Say |
|---|---|---|
| 0:00–0:20 | Problem slide (deck slide 2) or the one-pager | "16,000 O-1 petitions a year, 1 in 5 gets a Request for Evidence. We read 619 USCIS appeal decisions: in 63% of failures the evidence didn't show what the petition claimed." |
| 0:20–0:35 | How-it-works slide (slide 3) | "An Intern agent drafts the petition. Before it can finish, Drex checks every sentence against every exhibit. Flags go back to the agent. The attorney signs one receipt." |
| 0:35–1:05 | Race page, case **demo-run3**, press Race | "Same petition, three engines, started together. Drex: half a second. GPT-5.6 Sol reading the whole case: a few seconds. Vector search: slowest." Click claim 8: "'A team of dozens.' The org chart says six people. Only Drex says contradicted." |
| 1:05–1:35 | Pick **case04**, Race, open the vector-search miss | "This case is hand-labelled. Vector search never retrieved the paper, so it flagged a true sentence. Over 10 labelled cases: Drex 151 of 153, GPT 152, vector search 143, and Drex is 58× cheaper than GPT." |
| 1:35–1:55 | Optional: Upload PDFs (6 exhibits + `petition-draft-round1.md`) | "Any firm's PDFs: NDI parses them in under two seconds, then the same race." |
| 1:55–2:00 | Back to the race result | "CTM: agents write, Drex proves every sentence, the attorney signs." |

## Tips
- Say numbers exactly as on screen; live timings vary run to run, so read them off the stopwatch rather than quoting the deck.
- Never say "more accurate than GPT". Say "same accuracy, 4× faster, 58× cheaper".
- If a lane errors, keep going: the page shows the error instead of a fake number.
- Trim the start and end in QuickTime (Cmd+T) and export at 1080p. Upload as an unlisted YouTube or Loom link if the form asks for a URL.
