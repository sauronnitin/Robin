# Robin

[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-025E8C?logo=dependabot)](https://github.com/sauronnitin/Robin/security/dependabot)
[![CodeQL](https://github.com/sauronnitin/Robin/actions/workflows/codeql.yml/badge.svg)](https://github.com/sauronnitin/Robin/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/sauronnitin/Robin/badge)](https://scorecard.dev/viewer/?uri=github.com/sauronnitin/Robin)

Local-first AI **job application swarm**. Answer a few questions about the roles you want, drop in your resume and API keys, and run scout → fit → tailor → cover → apply agents from a visual canvas.

Dry-run is the default. Real applications are opt-in.

## 🚀 [**Try the live demo: click here**](https://sauronnitin.github.io/Robin/dashboard/mockup.html)

**No install, no signup, no API key.** Walk through the real onboarding
(with the animated bird) and every screen of the app (Browse, Apply board,
Knowledge Graph, Metrics), pre-filled with sample data. Nothing here is a
real job search; it's a static walkthrough hosted on GitHub Pages.

---

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/sauronnitin/Robin?quickstart=1)

Want the *real, running* app instead of the static demo? Click the badge
above for a live instance with zero local install: Python, Node,
Playwright, and the dashboard server are all set up automatically and the
onboarding screen opens in a preview tab (~30-90s to spin up, then it's a
real running copy of the app). Dry-run stays on and the shipped fictional
profile loads by default, so onboarding and the dashboard are usable
immediately. To go past onboarding into live Scout/Fit/Tailor/Cover runs,
open the `.env` file it creates for you and paste in your own
`GROQ_API_KEY` / `GEMINI_API_KEY` (see step 3 below for where to get them),
then restart the server from the terminal: `python dashboard/server.py`.

---

## What you get

- Role packs: Product Designer (example), Software Engineer, Product Manager, Data Analyst, Marketing
- Onboarding that previews which agents appear in your swarm
- Hybrid LLM routing: Gemini Flash for thinking agents, Groq 8B for tool agents
- Optional Google Drive + Sheets logging
- LinkedIn Easy Apply loop (separate canvas section) plus external Apply
- Dashboard canvas at `http://localhost:5959`

This repo ships a **fictional** Product Designer example (`profiles/product-designer/`). Your real resume and keys stay in gitignored `user/` and `.env`.

---

## Easiest path (10 minutes)

1. Open the [Pages onboarding](https://sauronnitin.github.io/Robin/docs/onboarding.html) (or [`docs/onboarding.html`](docs/onboarding.html) locally) and download `profile.json`.
2. Clone and install:

```bash
git clone https://github.com/sauronnitin/Robin.git
cd Robin
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -e .
playwright install chromium
```

3. Configure keys:

```bash
# Windows
copy .env.example .env
# macOS/Linux
# cp .env.example .env
```

Edit `.env`:

- `GROQ_API_KEY`
- `GEMINI_API_KEY` ([AI Studio](https://aistudio.google.com/apikey), Flash only)
- Keep `DRY_RUN=True`

4. Activate your profile:

```bash
mkdir user
move profile.json user\profile.json
# also place resume.pdf or resume.tex in user/
```

Or use the shipped example:

```env
ROBIN_PROFILE=product-designer
```

5. Run the dashboard:

```bash
python dashboard/server.py
```

Open [http://localhost:5959](http://localhost:5959).

6. (Recommended) Set up Drive: [docs/drive-setup.html](docs/drive-setup.html).

---

## Safety

| Setting | Meaning |
|---|---|
| `DRY_RUN=True` | Scout / tailor / draft only. No real submits. |
| `DRY_RUN=False` | Real Easy Apply / external Apply. You are responsible for what gets sent. |

Never commit `.env`, OAuth tokens, `browser-session/`, or `user/` uploads.

Found a vulnerability? See [`SECURITY.md`](SECURITY.md). Please don't file
it as a public issue.

---

## Gmail (optional)

Connecting Gmail is **optional**. The Apply board works without it: you move cards by hand.

If you connect it, Robin requests `gmail.readonly` only. It classifies recruiter replies (and can move a card from Applied to Replied). It cannot send, delete, or modify mail.

The token is stored locally as `gmail_token.json` (gitignored). Connect from Settings > Gmail replies, or `GET /api/gmail/connect` while the dashboard is running.

---

## Profiles and swarms

See [`profiles/README.md`](profiles/README.md).

Onboarding choices map to `swarm.modules` and `swarm.optional` (LinkedIn loop, cover letter, humanizer, Drive). Full “LLM invents new agents” is planned as C-lite expansion; V1 uses presets + module toggles.

---

## Stack

- Python 3.10-3.13, an AI agent framework, Playwright
- Dashboard: static HTML/JS + `dashboard/server.py`
- Auth for Drive: Google Desktop OAuth (not service accounts)

---

## Publish status (maintainers)

Live at `github.com/sauronnitin/Robin`. For anyone re-running this playbook
on a fork or a new template split-out:

1. Create public GitHub repo named `Robin`.
2. Push this tree (secrets already gitignored).
3. Settings → Pages → Deploy from branch → `main`, folder **`/ (root)`**,
   not `/docs`, since `dashboard/` needs to be reachable too.
4. Replace `sauronnitin` in `README.md` and `docs/*.html` with your own
   GitHub username.
5. Confirm the Pages URL opens both `docs/onboarding.html` and
   `dashboard/mockup.html` (the demo).
6. Click the Codespaces badge on a fresh clone; confirm the preview tab opens
   on the onboarding screen without manual setup, `.env` exists with
   `DRY_RUN=True`, and the API-key paste + restart step in the badge's
   description actually unlocks a live agent run.

---

## License

Copyright (c) 2026 Nitin Sauran. All rights reserved.

Robin is **not** open source. You may view this repo and run the Software on
your own machine for your own personal, non-commercial job search. You may
**not** copy it for others, sell it, republish it, or use it commercially
without prior written permission. Terms are in [LICENSE](LICENSE).
Third-party packages keep their own licenses ([NOTICE](NOTICE)).

Copies obtained under the previous MIT grant keep those MIT terms. This
proprietary license applies to this version and later.

Built as a generalization of a personal job-search pipeline. Example
candidate data is fictional.
