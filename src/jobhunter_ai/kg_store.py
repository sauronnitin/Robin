"""Knowledge Graph structured store (Individual + All dummy).

Source of truth is JSON schema under dashboard/kg defaults and gitignored
user/kg after clone. Chroma is not used here; optional vectors may come later
for document Q&A only.

Individual schema v2 adds targets (Primary/Secondary), compensation fields,
role_stats, and gap/band nodes for the career reality check.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_KG = _PROJECT_ROOT / "dashboard" / "kg"
_USER_KG = _PROJECT_ROOT / "user" / "kg"

_INDIVIDUAL_DEFAULT = _DASHBOARD_KG / "individual.default.json"
_ALL_DUMMY = _DASHBOARD_KG / "all.dummy.json"
_SALARY_BANDS = _DASHBOARD_KG / "salary_bands.us.json"
_INDIVIDUAL_USER = _USER_KG / "individual.json"
_SHARE_PREFS = _USER_KG / "share_prefs.json"
_ONET_DIR = _DASHBOARD_KG / "onet"
_ONET_OCCUPATIONS = _ONET_DIR / "occupations.json"
_ONET_AI_EXPOSURE = _ONET_DIR / "ai_exposure.json"
_ONET_RIASEC_ITEMS = _ONET_DIR / "riasec_items.json"
_RIASEC_USER = _USER_KG / "riasec.json"

_onet_cache: dict[str, Any] | None = None


def _load_onet() -> dict[str, Any]:
    global _onet_cache
    if _onet_cache is None:
        data = _read_json(_ONET_OCCUPATIONS) or {"occupations": []}
        _onet_cache = data
    return _onet_cache

# Curated skill expectations per role id (for deterministic fit/gap). Labels match
# common resume wording; matching is case-insensitive substring / slug aware.
_ROLE_SKILL_EXPECTATIONS: dict[str, list[str]] = {
    "role:product-designer": [
        "Figma",
        "Prototyping",
        "Design Systems",
        "User Research",
        "Interaction Design",
    ],
    "role:ux-designer": [
        "User Research",
        "Wireframing",
        "Usability Testing",
        "Figma",
        "Information Architecture",
    ],
    "role:ui-designer": [
        "Figma",
        "Visual Design",
        "Prototyping",
        "Design Systems",
        "Typography",
    ],
    "role:design-lead": [
        "Design Systems",
        "Figma",
        "Leadership",
        "User Research",
        "Mentoring",
    ],
    "opp:senior-pd": [
        "Figma",
        "Design Systems",
        "Prototyping",
        "Systems thinking",
        "User Research",
    ],
    "opp:design-ops": [
        "Design Systems",
        "Figma",
        "Process",
        "Storybook",
        "Governance",
    ],
}

_NODE_TYPES = frozenset(
    {"skill", "role", "company", "opp", "edu", "concept", "gap", "band"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _slug(prefix: str, label: str) -> str:
    bit = re.sub(r"[^a-z0-9]+", "-", str(label or "").lower()).strip("-")[:48]
    return f"{prefix}:{bit or 'item'}"


def empty_individual() -> dict[str, Any]:
    return {
        "version": 2,
        "kind": "individual",
        "updated_at": _utc_now(),
        "targets": {
            "primary_role_id": None,
            "secondary_role_id": None,
            "confirmed": False,
            "suggested_primary_id": None,
            "suggested_secondary_id": None,
        },
        "compensation": {
            "currency": "USD",
            "current": None,
            "target": None,
            "region": "US",
        },
        "role_stats": {},
        "nodes": [],
        "edges": [],
        "documents": [],
        "insights": {
            "summary": None,
            "node_briefs": {},
            "last_analyze": None,
        },
        "onboarding": {
            "completed": False,
            "started_at": None,
            "completed_at": None,
            "answers": [],
            "transcript": [],
        },
    }


def load_salary_bands() -> dict[str, Any]:
    """Load curated USA salary band estimates from dashboard/kg."""
    data = _read_json(_SALARY_BANDS)
    if not isinstance(data, dict):
        return {
            "version": 1,
            "region": "US",
            "currency": "USD",
            "source": "curated_estimate",
            "bands": [],
        }
    bands = data.get("bands") if isinstance(data.get("bands"), list) else []
    return {
        "version": int(data.get("version") or 1),
        "region": str(data.get("region") or "US"),
        "currency": str(data.get("currency") or "USD"),
        "source": str(data.get("source") or "curated_estimate"),
        "note": str(data.get("note") or ""),
        "bands": [b for b in bands if isinstance(b, dict) and b.get("id")],
    }


def _band_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for band in load_salary_bands().get("bands") or []:
        rid = str(band.get("role_id") or "").strip()
        if rid:
            out[rid] = band
        bid = str(band.get("id") or "").strip()
        if bid:
            out[bid] = band
    return out


def _normalize_targets(raw: Any) -> dict[str, Any]:
    base = {
        "primary_role_id": None,
        "secondary_role_id": None,
        "confirmed": False,
        "suggested_primary_id": None,
        "suggested_secondary_id": None,
    }
    if not isinstance(raw, dict):
        return base
    for key in base:
        if key == "confirmed":
            base[key] = bool(raw.get("confirmed"))
        elif raw.get(key) is not None and str(raw.get(key)).strip():
            base[key] = str(raw.get(key)).strip()
    return base


def _normalize_compensation(raw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "currency": "USD",
        "current": None,
        "target": None,
        "region": "US",
    }
    if not isinstance(raw, dict):
        return base
    if raw.get("currency"):
        base["currency"] = str(raw.get("currency")).strip() or "USD"
    if raw.get("region"):
        base["region"] = str(raw.get("region")).strip() or "US"
    for key in ("current", "target"):
        val = raw.get(key)
        if val is None or val == "":
            base[key] = None
            continue
        try:
            base[key] = int(float(val))
        except (TypeError, ValueError):
            base[key] = None
    return base


def _normalize_insights(raw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": None,
        "node_briefs": {},
        "last_analyze": None,
    }
    if raw is None:
        return base
    if isinstance(raw, str):
        base["summary"] = raw
        return base
    if not isinstance(raw, dict):
        return base
    if raw.get("summary") is not None:
        base["summary"] = str(raw.get("summary"))
    briefs = raw.get("node_briefs")
    if isinstance(briefs, dict):
        base["node_briefs"] = {
            str(k): (v if isinstance(v, (dict, str)) else str(v))
            for k, v in briefs.items()
        }
    if raw.get("last_analyze") is not None:
        base["last_analyze"] = raw.get("last_analyze")
    # Preserve extra Analyze sections (gaps, adjacent_roles, etc.) without forcing schema
    for key, val in raw.items():
        if key not in base:
            base[key] = val
    return base


def _normalize_onboarding(raw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "completed": False,
        "started_at": None,
        "completed_at": None,
        "answers": [],
        "transcript": [],
    }
    if not isinstance(raw, dict):
        return base
    base["completed"] = bool(raw.get("completed"))
    if raw.get("started_at"):
        base["started_at"] = str(raw.get("started_at"))
    if raw.get("completed_at"):
        base["completed_at"] = str(raw.get("completed_at"))
    answers = raw.get("answers")
    if isinstance(answers, list):
        base["answers"] = [a for a in answers if isinstance(a, dict)]
    transcript = raw.get("transcript")
    if isinstance(transcript, list):
        base["transcript"] = [t for t in transcript if isinstance(t, dict)]
    return base


def normalize_graph(raw: dict[str, Any] | None, *, kind: str) -> dict[str, Any]:
    base = empty_individual() if kind == "individual" else {
        "version": 1,
        "kind": "all",
        "updated_at": _utc_now(),
        "nodes": [],
        "edges": [],
        "documents": [],
        "insights": None,
        "hubs_primary": "skill",
        "hubs_secondary": "role_family",
        "privacy": {
            "mode": "opt_in_anonymized",
            "never": ["raw_resume", "chat", "email", "name", "exact_employer_dates"],
            "copy": (
                "Shared signals help improve JobHunter for everyone. "
                "We do not sell this data."
            ),
        },
    }
    if not isinstance(raw, dict):
        return base
    out = deepcopy(base)
    raw_version = int(raw.get("version") or 1)
    out["kind"] = kind
    out["updated_at"] = str(raw.get("updated_at") or _utc_now())
    nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    edges = raw.get("edges") if isinstance(raw.get("edges"), list) else []
    docs = raw.get("documents") if isinstance(raw.get("documents"), list) else []
    out["nodes"] = [_normalize_node(n) for n in nodes if isinstance(n, dict)]
    out["edges"] = [_normalize_edge(e) for e in edges if isinstance(e, dict)]
    out["documents"] = [d for d in docs if isinstance(d, dict)]

    if kind == "individual":
        out["version"] = 2 if raw_version < 2 else max(2, raw_version)
        out["targets"] = _normalize_targets(raw.get("targets"))
        out["compensation"] = _normalize_compensation(raw.get("compensation"))
        out["insights"] = _normalize_insights(raw.get("insights"))
        out["onboarding"] = _normalize_onboarding(raw.get("onboarding"))
        stats = raw.get("role_stats")
        out["role_stats"] = stats if isinstance(stats, dict) else {}
        out = compute_role_stats(out)
    else:
        out["version"] = raw_version if raw_version >= 1 else 1
        out["insights"] = raw.get("insights")
        out["hubs_primary"] = str(raw.get("hubs_primary") or "skill")
        out["hubs_secondary"] = str(raw.get("hubs_secondary") or "role_family")
        if isinstance(raw.get("privacy"), dict):
            out["privacy"] = raw["privacy"]
    return out


def _normalize_node(n: dict[str, Any]) -> dict[str, Any]:
    nid = str(n.get("id") or "").strip()
    label = str(n.get("label") or nid).strip()
    ntype = str(n.get("type") or "concept").strip()
    if ntype not in _NODE_TYPES:
        ntype = "concept"
    provenance = str(n.get("provenance") or "stated").strip().lower()
    if provenance not in ("stated", "inferred"):
        provenance = "stated"
    importance = str(n.get("importance") or "normal").strip().lower()
    if importance not in ("risk", "potential", "normal"):
        importance = "normal"
    try:
        weight = int(n.get("weight") or 2)
    except (TypeError, ValueError):
        weight = 2
    weight = max(1, min(5, weight))
    out: dict[str, Any] = {
        "id": nid,
        "label": label,
        "type": ntype,
        "weight": weight,
        "provenance": provenance,
        "importance": importance,
        "meta": n.get("meta") if isinstance(n.get("meta"), dict) else {},
    }
    if "count" in n:
        try:
            out["count"] = int(n.get("count") or 0)
        except (TypeError, ValueError):
            out["count"] = 0
    return out


def _normalize_edge(e: dict[str, Any]) -> dict[str, Any]:
    provenance = str(e.get("provenance") or "stated").strip().lower()
    if provenance not in ("stated", "inferred"):
        provenance = "stated"
    try:
        weight = int(e.get("weight") or 1)
    except (TypeError, ValueError):
        weight = 1
    out: dict[str, Any] = {
        "source": str(e.get("source") or "").strip(),
        "target": str(e.get("target") or "").strip(),
        "label": str(e.get("label") or "").strip(),
        "provenance": provenance,
        "weight": max(1, weight),
    }
    if "count" in e:
        try:
            out["count"] = int(e.get("count") or 0)
        except (TypeError, ValueError):
            out["count"] = 0
    return out


def _skill_match(skill_label: str, expected: str) -> bool:
    a = re.sub(r"[^a-z0-9]+", "", skill_label.lower())
    b = re.sub(r"[^a-z0-9]+", "", expected.lower())
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _norm_skill_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(label or "").lower())


def _user_skill_vector(nodes: list[dict[str, Any]]) -> dict[str, int]:
    """normalized_skill_key -> weight (1-5), keeping the highest weight if duplicated."""
    out: dict[str, int] = {}
    for n in _collect_skills(nodes):
        key = _norm_skill_key(str(n.get("label") or ""))
        if not key:
            continue
        w = int(n.get("weight") or 2)
        out[key] = max(out.get(key, 0), w)
    return out


def _occupation_ai_exposure(title: str) -> dict[str, Any]:
    data = _read_json(_ONET_AI_EXPOSURE) or {}
    t = str(title or "").lower()
    for rule in data.get("buckets") or []:
        if any(kw in t for kw in rule.get("match") or []):
            return {
                "score": rule.get("score"),
                "bucket": rule.get("bucket"),
                "note": data.get("note") or "",
            }
    default = data.get("default") or {"score": 0.4, "bucket": "Moderate"}
    return {
        "score": default.get("score"),
        "bucket": default.get("bucket"),
        "note": data.get("note") or "",
    }


def _lookup_user_level(user_vec: dict[str, int], skill_name: str) -> int | None:
    """Exact normalized key first, then substring soft-match for resume vs O*NET labels."""
    key = _norm_skill_key(skill_name)
    if key and key in user_vec:
        return user_vec[key]
    # Short keys (C, R, IM) cause false positives inside longer resume labels.
    if len(key) < 4:
        return None
    for ukey, weight in user_vec.items():
        if len(ukey) < 4:
            continue
        if ukey == key or ukey in key or key in ukey:
            return weight
    return None


def _title_tokens(text: str) -> set[str]:
    stop = {"and", "or", "of", "the", "a", "an", "except", "other"}
    tokens = set()
    for raw in re.findall(r"[a-z]+", (text or "").lower()):
        if raw in stop or len(raw) < 3:
            continue
        stem = raw[:-1] if raw.endswith("s") and len(raw) > 4 else raw
        tokens.add(stem)
    return tokens


def _title_adjacency_boost(graph: dict[str, Any], occ_title: str) -> float:
    """Boost occupations whose titles overlap stated role/opp labels (e.g. Product Designer)."""
    labels = [
        str(n.get("label") or "")
        for n in (graph.get("nodes") or [])
        if n.get("type") in ("role", "opp")
    ]
    ot = (occ_title or "").lower().strip()
    if not ot:
        return 0.0
    ot_tokens = _title_tokens(ot)
    best = 0.0
    for lab in labels:
        a = lab.lower().strip()
        if not a:
            continue
        if a in ot or ot in a:
            best = max(best, 0.7)
            continue
        at = _title_tokens(a)
        shared = at & ot_tokens
        if not shared:
            continue
        if len(shared) >= 2:
            best = max(best, 0.65)
        elif "design" in shared or "designer" in shared:
            best = max(best, 0.55)
        elif len(shared) == 1 and next(iter(shared)) not in {"product", "manager", "specialist"}:
            best = max(best, 0.35)
    return best


def _score_occupation(
    user_vec: dict[str, int],
    occ: dict[str, Any],
    *,
    title_boost: float = 0.0,
) -> dict[str, Any]:
    occ_skills = occ.get("skills") or []
    total_importance = sum(float(s.get("importance") or 0) for s in occ_skills) or 1.0
    matched_weight = 0.0
    matched: list[str] = []
    missing: list[str] = []
    for s in occ_skills:
        imp = float(s.get("importance") or 0)
        user_level = _lookup_user_level(user_vec, str(s.get("name") or ""))
        if user_level:
            matched_weight += min(imp, imp * (user_level / 5.0))
            matched.append(str(s.get("name")))
        else:
            missing.append(str(s.get("name")))
    demands_abilities_fit = round(min(1.0, matched_weight / total_importance), 3)
    # adjacency: coarse cosine-like overlap across the occupation's full skill set vs user vector
    occ_keys = {_norm_skill_key(str(s.get("name") or "")) for s in occ_skills}
    matched_keys = {
        _norm_skill_key(name)
        for name in matched
        if _norm_skill_key(name)
    }
    # Count soft matches toward overlap as well
    overlap = len(matched_keys) if matched_keys else len(occ_keys & set(user_vec.keys()))
    denom = (len(occ_keys) ** 0.5) * (len(user_vec) ** 0.5) or 1.0
    adjacency_score = round(min(1.0, (overlap / denom) + title_boost), 3)
    return {
        "soc_code": occ.get("soc_code"),
        "title": occ.get("title"),
        "job_zone": occ.get("job_zone"),
        "demands_abilities_fit": demands_abilities_fit,
        "adjacency_score": adjacency_score,
        "matched_skills": matched[:8],
        "missing_skills": missing[:8],
        "durability": _occupation_ai_exposure(str(occ.get("title") or "")),
    }


def _inferred_job_zone(graph: dict[str, Any]) -> int:
    """Rough readiness gate from years of experience-bearing role nodes; default 3."""
    roles = [n for n in (graph.get("nodes") or []) if n.get("type") == "role"]
    return 4 if len(roles) >= 3 else 3


def rank_adjacent_occupations(graph: dict[str, Any], top_n: int = 8) -> list[dict[str, Any]]:
    user_vec = _user_skill_vector(graph.get("nodes") or [])
    if not user_vec:
        return []
    zone_ceiling = _inferred_job_zone(graph) + 1
    scored = []
    for occ in _load_onet().get("occupations") or []:
        jz = occ.get("job_zone")
        if isinstance(jz, (int, float)) and jz > zone_ceiling:
            continue
        boost = _title_adjacency_boost(graph, str(occ.get("title") or ""))
        scored.append(_score_occupation(user_vec, occ, title_boost=boost))
    scored.sort(
        key=lambda s: (s["adjacency_score"], s["demands_abilities_fit"]),
        reverse=True,
    )
    return scored[:top_n]


def _collect_skills(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if n.get("type") == "skill" and n.get("id")]


def _role_candidates(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = [n for n in nodes if n.get("type") in ("role", "opp") and n.get("id")]
    roles.sort(
        key=lambda n: (
            0 if n.get("type") == "role" else 1,
            0 if n.get("provenance") == "stated" else 1,
            -(int(n.get("weight") or 0)),
            str(n.get("label") or ""),
        )
    )
    return roles


def suggest_targets(graph: dict[str, Any]) -> dict[str, Any]:
    """Auto-suggest Primary/Secondary from stated roles first, then opps."""
    targets = _normalize_targets(graph.get("targets"))
    roles = _role_candidates(graph.get("nodes") or [])
    stated_roles = [r for r in roles if r.get("type") == "role"]
    pool = stated_roles or roles
    suggested_primary = pool[0]["id"] if pool else None
    suggested_secondary = None
    for r in pool[1:]:
        if r["id"] != suggested_primary:
            suggested_secondary = r["id"]
            break
    targets["suggested_primary_id"] = suggested_primary
    targets["suggested_secondary_id"] = suggested_secondary
    if not targets.get("primary_role_id"):
        targets["primary_role_id"] = suggested_primary
    if not targets.get("secondary_role_id"):
        targets["secondary_role_id"] = suggested_secondary
    # Keep confirmed flag; if roles vanished, leave ids but do not invent new ones beyond suggest
    graph["targets"] = targets
    return graph


def _ensure_band_nodes(graph: dict[str, Any], role_ids: list[str]) -> None:
    bands = _band_index()
    nodes = graph.setdefault("nodes", [])
    existing = {n.get("id") for n in nodes if isinstance(n, dict)}
    for rid in role_ids:
        band = bands.get(rid)
        if not band:
            continue
        bid = str(band.get("id") or "")
        if not bid or bid in existing:
            continue
        nodes.append(
            {
                "id": bid,
                "label": f"{band.get('label') or rid} pay (estimate)",
                "type": "band",
                "weight": 2,
                "provenance": "stated",
                "importance": "normal",
                "meta": {
                    "low": band.get("low"),
                    "mid": band.get("mid"),
                    "high": band.get("high"),
                    "market": band.get("market") or "US",
                    "source": "curated_estimate",
                    "role_id": rid,
                    "currency": "USD",
                },
            }
        )
        existing.add(bid)
        edges = graph.setdefault("edges", [])
        if not any(
            e.get("source") == rid and e.get("target") == bid
            for e in edges
            if isinstance(e, dict)
        ):
            edges.append(
                {
                    "source": rid,
                    "target": bid,
                    "label": "pay estimate",
                    "provenance": "stated",
                    "weight": 1,
                }
            )


def _upsert_gap_node(
    graph: dict[str, Any],
    *,
    role_id: str,
    skill_label: str,
    reason: str,
) -> str:
    nodes = graph.setdefault("nodes", [])
    # Reuse existing gap for same role + skill (seeded or prior auto)
    for n in nodes:
        if n.get("type") != "gap":
            continue
        meta = n.get("meta") if isinstance(n.get("meta"), dict) else {}
        if meta.get("for_role") == role_id and _skill_match(
            str(meta.get("skill") or n.get("label") or ""), skill_label
        ):
            meta.setdefault("skill", skill_label)
            meta.setdefault("for_role", role_id)
            if reason and not meta.get("reason"):
                meta["reason"] = reason
            n["meta"] = meta
            n["importance"] = "risk"
            return str(n.get("id"))
    gap_id = _slug("gap", f"{role_id}-{skill_label}")
    existing = next((n for n in nodes if n.get("id") == gap_id), None)
    node = {
        "id": gap_id,
        "label": f"Gap: {skill_label}",
        "type": "gap",
        "weight": 3,
        "provenance": "inferred",
        "importance": "risk",
        "meta": {
            "skill": skill_label,
            "for_role": role_id,
            "reason": reason,
            "auto": True,
        },
    }
    if existing:
        existing.update(node)
    else:
        nodes.append(node)
    edges = graph.setdefault("edges", [])
    if not any(
        e.get("source") == gap_id and e.get("target") == role_id
        for e in edges
        if isinstance(e, dict)
    ):
        edges.append(
            {
                "source": gap_id,
                "target": role_id,
                "label": "needed for",
                "provenance": "inferred",
                "weight": 1,
            }
        )
    return gap_id


def _prune_stale_gaps(graph: dict[str, Any], keep_ids: set[str]) -> None:
    nodes = graph.get("nodes") or []
    kept_nodes: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "gap":
            meta = n.get("meta") if isinstance(n.get("meta"), dict) else {}
            # Keep seeded demo gaps or ones generated for current Primary/Secondary
            if n.get("id") in keep_ids or meta.get("seeded"):
                kept_nodes.append(n)
            continue
        kept_nodes.append(n)
    graph["nodes"] = kept_nodes
    keep_node_ids = {n.get("id") for n in kept_nodes}
    graph["edges"] = [
        e
        for e in (graph.get("edges") or [])
        if isinstance(e, dict)
        and e.get("source") in keep_node_ids
        and e.get("target") in keep_node_ids
    ]


def _score_role(
    role_id: str,
    skills: list[dict[str, Any]],
    graph: dict[str, Any],
) -> dict[str, Any]:
    expected = list(_ROLE_SKILL_EXPECTATIONS.get(role_id) or [])
    # Also pull neighbor skills linked to this role
    neighbor_skills: set[str] = set()
    for e in graph.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src == role_id or tgt == role_id:
            other = tgt if src == role_id else src
            sn = next((n for n in skills if n.get("id") == other), None)
            if sn:
                neighbor_skills.add(str(sn.get("label") or ""))

    stated_hits = 0
    inferred_hits = 0
    gap_ids: list[str] = []
    missing: list[str] = []

    for exp in expected:
        hit = None
        for s in skills:
            if _skill_match(str(s.get("label") or ""), exp):
                hit = s
                break
        if hit:
            if hit.get("provenance") == "inferred":
                inferred_hits += 1
            else:
                stated_hits += 1
            # Thin evidence: low weight or importance risk
            if (
                int(hit.get("weight") or 0) <= 2
                or hit.get("importance") == "risk"
                or (hit.get("meta") or {}).get("gap_hint")
            ):
                reason = str(
                    (hit.get("meta") or {}).get("gap_hint")
                    or "thin evidence for this skill"
                )
                gid = _upsert_gap_node(
                    graph, role_id=role_id, skill_label=exp, reason=reason
                )
                meta = next(
                    (n.get("meta") for n in graph["nodes"] if n.get("id") == gid),
                    {},
                )
                if isinstance(meta, dict):
                    meta["auto"] = True
                gap_ids.append(gid)
        else:
            missing.append(exp)
            gid = _upsert_gap_node(
                graph,
                role_id=role_id,
                skill_label=exp,
                reason=f"little or no evidence for {exp}",
            )
            meta = next(
                (n.get("meta") for n in graph["nodes"] if n.get("id") == gid),
                {},
            )
            if isinstance(meta, dict):
                meta["auto"] = True
            gap_ids.append(gid)

    total = max(1, len(expected) or 1)
    hit_score = (stated_hits + 0.5 * inferred_hits) / total
    fit_score = round(min(1.0, max(0.0, hit_score)), 2)
    # Stretch: room to grow toward senior / adjacent (inverse of fit, plus gap weight)
    stretch_score = round(min(1.0, max(0.0, 1.0 - fit_score * 0.7 + 0.1 * len(gap_ids) / total)), 2)

    bands = _band_index()
    band = bands.get(role_id) or {}
    band_id = str(band.get("id") or "") or None

    return {
        "fit_score": fit_score,
        "stretch_score": stretch_score,
        "gap_ids": gap_ids,
        "band_id": band_id,
        "stated_skill_hits": stated_hits,
        "inferred_skill_hits": inferred_hits,
        "expected_skills": expected,
        "missing_skills": missing,
        "neighbor_skill_count": len(neighbor_skills),
    }


def compute_role_stats(graph: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Primary/Secondary suggestions, gaps, and pay band links.

    No LLM. Comp current/target stay user-entered; bands are curated estimates.
    """
    if not isinstance(graph, dict) or graph.get("kind") != "individual":
        return graph
    graph = suggest_targets(graph)
    targets = graph.get("targets") or {}
    primary = targets.get("primary_role_id")
    secondary = targets.get("secondary_role_id")
    focus_ids = [rid for rid in (primary, secondary) if rid]

    skills = _collect_skills(graph.get("nodes") or [])
    keep_gaps: set[str] = set()
    role_stats: dict[str, Any] = {}

    # Seeded gaps (meta.seeded) always kept
    for n in graph.get("nodes") or []:
        if (
            isinstance(n, dict)
            and n.get("type") == "gap"
            and (n.get("meta") or {}).get("seeded")
        ):
            keep_gaps.add(str(n.get("id")))

    for rid in focus_ids:
        # Ensure role node exists before scoring
        if not any(n.get("id") == rid for n in (graph.get("nodes") or [])):
            continue
        stats = _score_role(rid, skills, graph)
        role_stats[rid] = stats
        keep_gaps.update(stats.get("gap_ids") or [])
        stats["demands_abilities_fit"] = None
        stats["adjacency_candidates"] = []
        stats["durability"] = None
    adjacency = rank_adjacent_occupations(graph, top_n=8)
    if adjacency and primary in role_stats:
        role_stats[primary]["adjacency_candidates"] = adjacency
        role_stats[primary]["durability"] = adjacency[0].get("durability")
        top_da = max((a["demands_abilities_fit"] for a in adjacency), default=None)
        if top_da is not None:
            role_stats[primary]["demands_abilities_fit"] = top_da

    _prune_stale_gaps(graph, keep_gaps)
    _ensure_band_nodes(graph, focus_ids)

    # Re-attach gap ids after prune (seeded + auto kept)
    for rid, stats in role_stats.items():
        live = [
            n.get("id")
            for n in (graph.get("nodes") or [])
            if n.get("type") == "gap"
            and (n.get("meta") or {}).get("for_role") == rid
        ]
        stats["gap_ids"] = live

    graph["role_stats"] = role_stats

    # Default insight summary when empty (store-driven reality check)
    insights = _normalize_insights(graph.get("insights"))
    if not insights.get("summary"):
        insights["summary"] = _default_summary(graph)
    graph["insights"] = insights
    return graph


def _default_summary(graph: dict[str, Any]) -> str:
    targets = graph.get("targets") or {}
    primary = targets.get("primary_role_id")
    secondary = targets.get("secondary_role_id")
    stats = (graph.get("role_stats") or {}).get(primary or "") or {}
    nodes = {n.get("id"): n for n in (graph.get("nodes") or []) if n.get("id")}
    p_label = (nodes.get(primary) or {}).get("label") or primary or "your target role"
    s_label = (nodes.get(secondary) or {}).get("label") or ""
    fit = stats.get("fit_score")
    gaps = stats.get("gap_ids") or []
    fit_pct = int(round(float(fit or 0) * 100)) if fit is not None else None
    parts = [
        f"Suggested Primary: {p_label}"
        + (f". Secondary: {s_label}" if s_label else "")
        + "."
    ]
    if fit_pct is not None:
        parts.append(f"Fit about {fit_pct}% based on skills already on your graph.")
    if gaps:
        parts.append(f"{len(gaps)} gap(s) to close for Primary.")
    comp = graph.get("compensation") or {}
    band_id = stats.get("band_id")
    band_node = nodes.get(band_id) if band_id else None
    mid = None
    if band_node and isinstance(band_node.get("meta"), dict):
        mid = band_node["meta"].get("mid")
    if mid:
        parts.append(f"USA pay estimate mid around ${int(mid):,} (curated estimate).")
    if comp.get("target"):
        delta = (mid or 0) - int(comp["target"]) if mid else None
        if delta is not None:
            if delta >= 0:
                parts.append(
                    f"Estimate mid is about ${abs(int(delta)):,} above your target."
                )
            else:
                parts.append(
                    f"Your target is about ${abs(int(delta)):,} above the estimate mid."
                )
    if not targets.get("confirmed"):
        parts.append("Confirm Primary when it looks right, or Change either role.")
    return " ".join(parts)


def _is_thin_individual(graph: dict[str, Any]) -> bool:
    """True when user save lacks career roles/skills (failed upload wipe, etc.)."""
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    has_role = any(n.get("type") in ("role", "opp") for n in nodes)
    has_skill = any(n.get("type") == "skill" for n in nodes)
    return not has_role and not has_skill


def _merge_seed_into_thin(user_graph: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    """Keep user docs/comp/targets overrides; restore seed career nodes when thin."""
    out = deepcopy(seed)
    # Preserve user documents and any concept/doc nodes they added
    user_docs = user_graph.get("documents") if isinstance(user_graph.get("documents"), list) else []
    out["documents"] = [d for d in user_docs if isinstance(d, dict)]
    user_comp = user_graph.get("compensation")
    if isinstance(user_comp, dict):
        out["compensation"] = _normalize_compensation(user_comp)
    # Merge extra user nodes (e.g. uploaded doc concepts) without dropping seed roles
    seen = {n.get("id") for n in (out.get("nodes") or []) if isinstance(n, dict)}
    for n in user_graph.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id") or n.get("id") in seen:
            continue
        out.setdefault("nodes", []).append(_normalize_node(n))
        seen.add(n.get("id"))
    # Prefer user confirmation state when they had already confirmed (rare on thin)
    ut = _normalize_targets(user_graph.get("targets"))
    if ut.get("confirmed") and ut.get("primary_role_id"):
        out["targets"] = ut
    out["updated_at"] = _utc_now()
    return normalize_graph(out, kind="individual")


def load_individual() -> dict[str, Any]:
    """Prefer user/kg after clone; fall back to committed default dummy.

    If the user file exists but is thin (no roles/skills), merge the seed career
    graph back in so a failed PDF upload cannot blank the reality check.
    """
    # #region agent log
    _dbg_path = _PROJECT_ROOT / "debug-a25fc8.log"
    # #endregion
    user = _read_json(_INDIVIDUAL_USER)
    if user:
        normalized = normalize_graph(user, kind="individual")
        thin = _is_thin_individual(normalized)
        # #region agent log
        try:
            with _dbg_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "sessionId": "a25fc8",
                            "runId": "pre",
                            "hypothesisId": "A",
                            "location": "kg_store.py:load_individual",
                            "message": "user graph load",
                            "data": {
                                "nodes": len(normalized.get("nodes") or []),
                                "thin": thin,
                                "primary": (normalized.get("targets") or {}).get(
                                    "primary_role_id"
                                ),
                            },
                            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
        # #endregion
        if thin:
            seed = _read_json(_INDIVIDUAL_DEFAULT) or empty_individual()
            merged = _merge_seed_into_thin(normalized, normalize_graph(seed, kind="individual"))
            # Persist repair so the next load is not thin again
            try:
                _write_json(_INDIVIDUAL_USER, merged)
            except OSError:
                pass
            # #region agent log
            try:
                with _dbg_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "sessionId": "a25fc8",
                                "runId": "pre",
                                "hypothesisId": "A",
                                "location": "kg_store.py:load_individual",
                                "message": "merged seed into thin user graph",
                                "data": {
                                    "nodes": len(merged.get("nodes") or []),
                                    "primary": (merged.get("targets") or {}).get(
                                        "primary_role_id"
                                    ),
                                },
                                "timestamp": int(
                                    datetime.now(timezone.utc).timestamp() * 1000
                                ),
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass
            # #endregion
            return merged
        return normalized
    default = _read_json(_INDIVIDUAL_DEFAULT) or empty_individual()
    return normalize_graph(default, kind="individual")


def save_individual(body: dict[str, Any] | None) -> dict[str, Any]:
    """Persist Individual graph under user/kg (created on first save after clone)."""
    graph = normalize_graph(body if isinstance(body, dict) else {}, kind="individual")
    if _is_thin_individual(graph):
        seed = _read_json(_INDIVIDUAL_DEFAULT) or empty_individual()
        graph = _merge_seed_into_thin(graph, normalize_graph(seed, kind="individual"))
    graph["updated_at"] = _utc_now()
    _write_json(_INDIVIDUAL_USER, graph)
    return graph


def load_all() -> dict[str, Any]:
    """Admin All aggregate. Dummy file for now; live opt-in feed later."""
    data = _read_json(_ALL_DUMMY) or {}
    return normalize_graph(data, kind="all")


def load_share_prefs() -> dict[str, Any]:
    data = _read_json(_SHARE_PREFS)
    if isinstance(data, dict):
        return data
    return {
        "opt_in_all": False,
        "updated_at": None,
        "copy": (
            "Shared signals help improve JobHunter for everyone. "
            "We do not sell this data."
        ),
    }


def save_share_prefs(body: dict[str, Any] | None) -> dict[str, Any]:
    prefs = load_share_prefs()
    if isinstance(body, dict):
        if "opt_in_all" in body:
            prefs["opt_in_all"] = bool(body.get("opt_in_all"))
    prefs["updated_at"] = _utc_now()
    _write_json(_SHARE_PREFS, prefs)
    return prefs
