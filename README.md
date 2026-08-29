# JobCrew

Local-first AI **job application swarm**. Answer a few questions about the roles you want, drop in your resume and API keys, and run scout → fit → tailor → cover → apply agents from a visual canvas.

Dry-run is the default. Real applications are opt-in.

> **Site (GitHub Pages):** after you publish, open  
> `https://YOUR_GITHUB_USER.github.io/JobCrew/`  
> Start with **[Onboarding](docs/onboarding.html)** → download `profile.json` → follow **[Install](docs/install.html)**.

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

1. Open the Pages onboarding (or [`docs/onboarding.html`](docs/onboarding.html) locally) and download `profile.json`.
2. Clone and install:

```bash
git clone https://github.com/YOUR_GITHUB_USER/JobCrew.git
cd JobCrew
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
JOBCREW_PROFILE=product-designer
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

---

## Gmail (optional)

Connecting Gmail is **optional**. The Apply board works without it: you move cards by hand.

If you connect it, JobCrew requests `gmail.readonly` only. It classifies recruiter replies (and can move a card from Applied to Replied). It cannot send, delete, or modify mail.

The token is stored locally as `gmail_token.json` (gitignored). Connect from Settings > Gmail replies, or `GET /api/gmail/connect` while the dashboard is running.

---

## Profiles and swarms

See [`profiles/README.md`](profiles/README.md).

Onboarding choices map to `swarm.modules` and `swarm.optional` (LinkedIn loop, cover letter, humanizer, Drive). Full “LLM invents new agents” is planned as C-lite expansion; V1 uses presets + module toggles.

---

## Stack

- Python 3.10–3.13, CrewAI, Playwright
- Dashboard: static HTML/JS + `dashboard/server.py`
- Auth for Drive: Google Desktop OAuth (not service accounts)

---

## Publish checklist (maintainers)

1. Create public GitHub repo named `JobCrew`.
2. Push this tree (secrets already gitignored).
3. Settings → Pages → Deploy from branch → `/docs`.
4. Replace `YOUR_GITHUB_USER` in `README.md` and `docs/*.html`.
5. Confirm Pages URL opens onboarding.

---

## License

MIT (add `LICENSE` when you publish). Built as a generalization of a personal JobHunter pipeline; example candidate data is fictional.
