# Reference — Source Documents

Read-only. These are the original source-of-truth files from the Studio build.
Do not edit. Use `.planning/BUILD_PLAN.md` for the local build spec.

---

| File | What it is |
|---|---|
| `case-study-summary.md` | High-level §1–§16 summary (short version) |
| `case-study-full.md` | Full narrative including verbatim Studio chatbox transcript (§1–§16) |
| `migration-plan.md` | The local migration plan that preceded this build |
| `studio-export/` | Actual CrewAI Studio export — agents.yaml, tasks.yaml, crew.py, custom tools |

The `studio-export/` folder is the **canonical source** for:
- `agents.yaml` — port verbatim to `src/robin/config/agents.yaml` (one goal fix)
- `tasks.yaml` — port verbatim to `src/robin/config/tasks.yaml` (zero changes)
- `tools/latex_to_pdf_compiler.py` — port verbatim (zero changes)
