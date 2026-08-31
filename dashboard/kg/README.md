# Knowledge Graph data (dashboard/kg)

Committed **defaults / dummies** for the public repo.

| File | Purpose |
|------|---------|
| `individual.default.json` | Demo Individual graph (v2: targets, compensation, gaps, bands) |
| `salary_bands.us.json` | Curated USA pay estimates (labeled estimates, not quotes) |
| `all.dummy.json` | Admin All aggregate preview (anonymized counts) |

After someone clones and runs the dashboard, Individual writes go to gitignored:

`user/kg/individual.json`

via `GET/POST /api/kg/individual` (`src/robin/kg_store.py`).
`GET /api/kg/salary-bands` serves the curated band file.

Raw resumes and chat never land in All. Opt-in share prefs: `user/kg/share_prefs.json` (later).
