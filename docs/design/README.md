# UX/UI structure wireframes · v0.1 (2026-09-02)

Design canvas (view, comment, export PNG/PDF): https://claude.ai/code/artifact/05fc0d16-af7a-4ce5-8819-2e1cef740847

11 artboards: `Structure` (sitemap, global states, key flows) + Dashboard (`Main`), Strategies, Research, Journal, AI, Risk, Reports, Events, Settings, KillFlow.
Static, low-fi, structure only. Visual design (theme, colors, typography) is decided in Phase 2 when the Electron shell exists.

## Regenerate

```bash
python docs/design/wireframes/gen.py        # writes *.dc.html + canvas.json next to it
```

The `.dc.html` files are self-contained HTML (Google Font "Mali"). Open one in a browser to preview; the `support.js` reference is only used by the canvas editor and can be ignored.

## Decisions carried by these wireframes

- Connection profiles (Demo / Live) as separate cores; top-bar color follows the mode (D8)
- Nine-page left nav; KILL always in the top bar; killed state shows a banner on every page (D12)
- Strategies list with variants A/B/C and a lifecycle stepper whose Promote button unlocks only when gates are met (D9, D10)
- Journal rows are decisions, including rejected and blocked ones; the detail panel is a six-step decision chain
- AI page exposes only regime / bias / size_mult / block with expiry; calendar block works without the LLM (D6)
- Risk values read-only while RUNNING; edits require PAUSED and a logged reason
- Settings shows secrets as status only; nothing is typed into the UI (rule 8)
