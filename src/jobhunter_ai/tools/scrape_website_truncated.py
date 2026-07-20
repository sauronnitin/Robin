import json
import re

from crewai_tools import ScrapeWebsiteTool

from jobhunter_ai.truncate import truncate_for_llm

# Keep each fetch small enough that a few tool rounds stay under Groq 8B
# free-tier TPM (~6k). Prefer structured truncation for JSON APIs.
MAX_CHARS = 1600
MAX_JSON_JOBS = 4
MAX_DESC_CHARS = 120
MAX_TAGS = 4


def _truncate_json_payload(text: str) -> str | None:
    """If the scrape looks like a RemoteOK-style JSON array, return a compact listing."""
    # Strip the ScrapeWebsiteTool preamble if present
    body = text
    marker = "The following text is scraped website content:\n\n"
    if body.startswith(marker):
        body = body[len(marker) :]

    body = body.strip()
    if not (body.startswith("[") or body.startswith("{")):
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Sometimes HTML wrappers leak in; try to find a JSON array substring
        match = re.search(r"\[.*\]", body, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if isinstance(data, dict):
        # Some APIs wrap jobs under a key
        for key in ("jobs", "data", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return None

    if not isinstance(data, list):
        return None

    compact = []
    for item in data:
        if not isinstance(item, dict):
            continue
        # RemoteOK legal notice object has no position/company
        title = item.get("position") or item.get("title") or item.get("name")
        company = item.get("company") or item.get("company_name")
        if not title and not company:
            continue
        desc = item.get("description") or item.get("text") or ""
        if isinstance(desc, str):
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:MAX_DESC_CHARS]
        tags = item.get("tags") or item.get("skills") or []
        if isinstance(tags, list):
            tags = tags[:MAX_TAGS]
        compact.append(
            {
                "title": title,
                "company": company,
                "location": item.get("location") or item.get("candidate_required_location") or "",
                "tags": tags,
                "url": item.get("url") or item.get("apply_url") or item.get("link") or "",
                "description": desc,
            }
        )
        if len(compact) >= MAX_JSON_JOBS:
            break

    if not compact:
        return None

    payload = (
        "Compact job listings extracted from scraped JSON "
        f"(showing up to {MAX_JSON_JOBS}):\n"
        + json.dumps(compact, ensure_ascii=True, indent=None)
    )
    return truncate_for_llm(payload, MAX_CHARS)


class TruncatedScrapeWebsiteTool(ScrapeWebsiteTool):
    """ScrapeWebsiteTool, capped for Groq free-tier TPM.

    Some job board endpoints (e.g. remoteok.com's API) return huge unfiltered
    dumps. Left uncapped, a handful of fetches in one task blows past the LLM
    context / tokens-per-minute budget.
    """

    def _run(self, **kwargs):
        text = super()._run(**kwargs)
        compact = _truncate_json_payload(text)
        if compact is not None:
            return compact
        return truncate_for_llm(text, MAX_CHARS)
