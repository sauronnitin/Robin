# Open Resume Parser (AGPL sidecar)

Isolated Node CLI that runs the [Open Resume](https://github.com/xitanggg/open-resume) PDF parser and prints structured JSON. Robin calls this via subprocess; AGPL sources stay in this folder only.

## License

**AGPL-3.0.** See `LICENSE` and `NOTICE`. Do not copy these sources into `src/robin/`.

## Setup

Requires **Node.js 18+**.

```bash
cd tools/open-resume-parser
npm install
```

## CLI

```bash
node parse.mjs /path/to/resume.pdf
```

Stdout is a single JSON object:

```json
{ "ok": true, "resume": { "profile": {}, "workExperiences": [], "educations": [], "projects": [], "skills": {}, "custom": {} } }
```

On failure:

```json
{ "ok": false, "error": "..." }
```

The PDF must have an extractable text layer (not a scanned image-only PDF).
