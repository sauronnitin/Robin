# Profiles

Each folder under `profiles/` is a Robin **role pack**.

## Layout

```
profiles/
  product-designer/     # shipped example (fictional candidate)
    profile.json
    resume.tex
  software-engineer/    # Jordan Lee (fictional)
    profile.json
    resume.tex
  product-manager/      # Sam Okonkwo (fictional)
    profile.json
    resume.tex
  data-analyst/         # Casey Nguyen (fictional)
    profile.json
    resume.tex
  marketing/            # Riley Chen (fictional)
    profile.json
    resume.tex
```

Your private uploads after onboarding go in `user/` (gitignored):

```
user/
  profile.json          # active profile (copied/merged from a preset)
  resume.pdf            # or resume.tex
  secrets/              # optional local OAuth client JSON
```

## Activate a profile

1. Copy a preset into `user/`, or run the onboarding wizard.
2. Set in `.env`:

```env
ROBIN_PROFILE=product-designer
```

If `user/profile.json` exists, it wins over the preset id.

## Swarm modules

`profile.json` → `swarm.modules` and `swarm.optional` control which agent cards appear on the canvas (Phase B/C). Presets already encode sensible defaults per role family.
