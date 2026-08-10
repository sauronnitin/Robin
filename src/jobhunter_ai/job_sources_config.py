"""Company watchlist + free-source targets for Browse / Scout.

Config lives in ``user/job_sources.json`` (gitignored user dir). Defaults match
the Profile "Company watchlist" / "Free source targets" textareas:

  greenhouse,<slug>
  lever,<slug>
  ashby,<slug>
  workable,<slug>
  https://careers.<domain>/jobs

  github:<query>
  hn:<query>
  reddit:<sub>:<query>
  ats:<provider>:<slug>
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_USER_PATH = _PROJECT_ROOT / "user" / "job_sources.json"
_DEFAULT_PATH = _PROJECT_ROOT / "dashboard" / "job_sources.default.json"

# Browse / Scout source ids shown in the API filter (exclude LinkedIn).
SOURCE_CATALOG: list[dict[str, str]] = [
    {"id": "greenhouse", "label": "Greenhouse", "group": "ats"},
    {"id": "lever", "label": "Lever", "group": "ats"},
    {"id": "ashby", "label": "Ashby", "group": "ats"},
    {"id": "workable", "label": "Workable", "group": "ats"},
    {"id": "remoteok", "label": "RemoteOK", "group": "open"},
    {"id": "remotive", "label": "Remotive", "group": "open"},
    {"id": "jobicy", "label": "Jobicy", "group": "open"},
    {"id": "arbeitnow", "label": "Arbeitnow", "group": "open"},
    {"id": "himalayas", "label": "Himalayas", "group": "open"},
    {"id": "themuse", "label": "The Muse", "group": "open"},
    {"id": "freehire", "label": "Freehire", "group": "open"},
    {"id": "rise", "label": "Rise", "group": "open"},
    {"id": "fourdayweek", "label": "4 Day Week", "group": "open"},
    {"id": "github", "label": "GitHub Jobs", "group": "community"},
    {"id": "hn", "label": "Hacker News", "group": "community"},
    {"id": "reddit", "label": "Reddit", "group": "community"},
]

DEFAULT_ENABLED = [
    "greenhouse",
    "lever",
    "ashby",
    "remoteok",
    "remotive",
    "jobicy",
    "arbeitnow",
    "himalayas",
    "themuse",
    "github",
    "hn",
]

DEFAULT_WATCHLIST = [
    "greenhouse,anthropic",
    "greenhouse,stripe",
    "greenhouse,vercel",
    "greenhouse,linear",
    "greenhouse,notion",
    "greenhouse,figma",
    "lever,vercel",
    "lever,loom",
    "lever,pitch",
    "ashby,linear",
    "ashby,vercel",
    "workable,gitlab",
]

DEFAULT_FREE_TARGETS = [
    "github:product designer hiring help wanted",
    "hn:product designer remote hiring",
    "reddit:forhire:product designer hiring remote",
    "ats:greenhouse:stripe",
]


def default_config() -> dict[str, Any]:
    return {
        "company_watchlist": list(DEFAULT_WATCHLIST),
        "free_source_targets": list(DEFAULT_FREE_TARGETS),
        "enabled_sources": list(DEFAULT_ENABLED),
        "discovered_apis": [],
        "last_scan_at": "",
        "last_scan_report": {},
    }


def _coerce_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [ln.strip() for ln in value.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            s = str(item or "").strip()
            if s and not s.startswith("#"):
                out.append(s)
        return out
    return []


def _known_source_ids(raw: dict[str, Any] | None = None) -> set[str]:
    known = {s["id"] for s in SOURCE_CATALOG}
    if isinstance(raw, dict):
        for item in raw.get("discovered_apis") or []:
            if isinstance(item, dict) and item.get("id"):
                known.add(str(item["id"]).strip().lower())
    return known


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_config()
    watch = _coerce_lines(raw.get("company_watchlist"))
    free = _coerce_lines(raw.get("free_source_targets"))
    enabled = raw.get("enabled_sources")
    known = _known_source_ids(raw)
    if isinstance(enabled, list) and enabled:
        enabled_ids = [str(x).strip().lower() for x in enabled if str(x).strip().lower() in known]
    else:
        enabled_ids = list(DEFAULT_ENABLED)
    discovered = []
    for item in raw.get("discovered_apis") or []:
        if isinstance(item, dict) and item.get("id"):
            discovered.append(item)
    report = raw.get("last_scan_report")
    if not isinstance(report, dict):
        report = {}
    return {
        "company_watchlist": watch or list(DEFAULT_WATCHLIST),
        "free_source_targets": free or list(DEFAULT_FREE_TARGETS),
        "enabled_sources": enabled_ids or list(DEFAULT_ENABLED),
        "discovered_apis": discovered,
        "last_scan_at": str(raw.get("last_scan_at") or ""),
        "last_scan_report": report,
    }


def load_job_sources(path: Path | None = None) -> dict[str, Any]:
    target = path or _USER_PATH
    if not target.exists() and _DEFAULT_PATH.exists():
        target = _DEFAULT_PATH
    if not target.exists():
        return default_config()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()
    return normalize_config(data if isinstance(data, dict) else None)


def save_job_sources(payload: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or _USER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    merged = dict(existing) if isinstance(existing, dict) else {}
    if isinstance(payload, dict):
        merged.update(payload)
    cfg = normalize_config(merged)
    target.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg


def extended_catalog(cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Static catalog plus any discovered open APIs not already listed."""
    data = cfg or load_job_sources()
    out = [dict(s) for s in SOURCE_CATALOG]
    seen = {s["id"] for s in out}
    for item in data.get("discovered_apis") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip().lower()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(
            {
                "id": sid,
                "label": str(item.get("label") or sid),
                "group": str(item.get("group") or "open"),
            }
        )
    return out


def catalog_payload() -> dict[str, Any]:
    cfg = load_job_sources()
    return {
        "ok": True,
        "sources": extended_catalog(cfg),
        "enabled_sources": cfg.get("enabled_sources") or DEFAULT_ENABLED,
        "company_watchlist": cfg.get("company_watchlist") or [],
        "free_source_targets": cfg.get("free_source_targets") or [],
        "discovered_apis": cfg.get("discovered_apis") or [],
        "last_scan_at": cfg.get("last_scan_at") or "",
        "last_scan_report": cfg.get("last_scan_report") or {},
        "groups": {
            "ats": "Company ATS boards",
            "open": "Open job APIs (no key)",
            "community": "Community hiring posts",
        },
    }


def parse_watchlist_line(line: str) -> dict[str, str] | None:
    """Parse ``provider,slug`` or a careers URL into a structured entry."""
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.lower().startswith("http://") or raw.lower().startswith("https://"):
        return {"kind": "url", "provider": "url", "slug": "", "url": raw}
    if "," not in raw:
        return None
    provider, slug = raw.split(",", 1)
    provider = provider.strip().lower()
    slug = slug.strip().strip("/")
    if provider not in {"greenhouse", "lever", "ashby", "workable"} or not slug:
        return None
    return {"kind": "ats", "provider": provider, "slug": slug, "url": ""}


def parse_free_target(line: str) -> dict[str, str] | None:
    """Parse ``github:``, ``hn:``, ``reddit:``, ``ats:`` free-source lines."""
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    lower = raw.lower()
    if lower.startswith("github:"):
        return {"kind": "github", "query": raw.split(":", 1)[1].strip()}
    if lower.startswith("hn:"):
        return {"kind": "hn", "query": raw.split(":", 1)[1].strip()}
    if lower.startswith("reddit:"):
        rest = raw.split(":", 1)[1].strip()
        if ":" in rest:
            sub, query = rest.split(":", 1)
            return {"kind": "reddit", "subreddit": sub.strip() or "forhire", "query": query.strip()}
        return {"kind": "reddit", "subreddit": "forhire", "query": rest}
    if lower.startswith("ats:"):
        rest = raw.split(":", 1)[1].strip()
        parts = [p.strip() for p in rest.split(":") if p.strip()]
        if len(parts) >= 2:
            return {"kind": "ats", "provider": parts[0].lower(), "slug": parts[1]}
    return None


def watchlist_slugs(cfg: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Return ``{provider: [slug, ...]}`` from watchlist + ats: free targets."""
    data = cfg or load_job_sources()
    out: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": [], "workable": []}
    for line in data.get("company_watchlist") or []:
        parsed = parse_watchlist_line(str(line))
        if not parsed or parsed.get("kind") != "ats":
            continue
        prov = parsed["provider"]
        slug = parsed["slug"]
        if prov in out and slug not in out[prov]:
            out[prov].append(slug)
    for line in data.get("free_source_targets") or []:
        parsed = parse_free_target(str(line))
        if not parsed or parsed.get("kind") != "ats":
            continue
        prov = parsed.get("provider") or ""
        slug = parsed.get("slug") or ""
        if prov in out and slug and slug not in out[prov]:
            out[prov].append(slug)
    return out


def free_queries(cfg: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Return query lists for github / hn / reddit free targets."""
    data = cfg or load_job_sources()
    out: dict[str, list[str]] = {"github": [], "hn": [], "reddit": []}
    for line in data.get("free_source_targets") or []:
        parsed = parse_free_target(str(line))
        if not parsed:
            continue
        kind = parsed.get("kind")
        if kind == "github" and parsed.get("query"):
            out["github"].append(parsed["query"])
        elif kind == "hn" and parsed.get("query"):
            out["hn"].append(parsed["query"])
        elif kind == "reddit" and parsed.get("query"):
            sub = parsed.get("subreddit") or "forhire"
            out["reddit"].append(f"{sub}:{parsed['query']}")
    return out
