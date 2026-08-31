# Robin — Linear canvas dashboard

Figma-like pan/zoom canvas with editable AI agent cards, live DAG edges, activity log, and token usage. Visual language follows [Linear DESIGN.md](https://www.shadcn.io/design/linear) (canvas `#010102`, lavender `#5e6ad2`, surface ladder, Inter + JetBrains Mono).

## Run

```
python dashboard/server.py
```

Open **http://localhost:5959**

Prior Bugatti UI: `python dashboard-archive/serve.py v18-bugatti-error-bus`

## Controls

| Action | How |
|--------|-----|
| Zoom | Pinch / ctrl+wheel, or corner **+ / − / Fit / 1:1** |
| Pan | Space+drag or middle-mouse drag |
| Marquee select | Drag on empty canvas |
| Multi-select | Shift+click cards |
| Group section | Select 2+ cards, then **Ctrl+Shift+S** |
| Ungroup | Select section, **Ctrl+Shift+U** or Ungroup button |
| Move section | Drag section title chrome (children move together) |
| Rename section | Double-click section name |
| Detach from section | Drag a child card until its center leaves the frame |
| Move card | Drag the card chrome (top handle bar) |
| Edit fields | Click Role / Goal / … (lavender focus). Enter or blur saves to localStorage |
| Expand card | Click card body — shows backstory, task description, expected output, llm, max_iter, max_rpm, tools |
| Expand Activity | Expand icon on the Activity panel |
| Reset Run | Clears simulation only |
| Reset Layout | Re-arranges cards left-to-right and fits view |
| Reset Cards | Restores all field text from `pipeline-data.js` |

## Data

`pipeline-data.js` mirrors the agent/task definitions in `agents.yaml`,
`tasks.yaml`, and `crew.py`. Update it when those change.

## Mode

Start tries a live Robin run (`POST /api/run`); falls back to sim if launch fails. Edges animate on the active hop.
