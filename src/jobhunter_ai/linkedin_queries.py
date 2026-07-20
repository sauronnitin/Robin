"""LinkedIn alert query set and geo priority for the LinkedIn agentic loop."""

from __future__ import annotations

# Exact alert queries (do not rephrase). Scout encodes these into LinkedIn Jobs search.
LINKEDIN_ALERT_QUERIES: list[str] = [
    'entry-level or manager or senior ("Product Designer" OR "UX Designer") AND ("AI" OR "GenAI" OR "LLM" OR "machine learning") contract or full-time or part-time posted in the past 24 hours',
    '"Design Lead" OR "Product Design Lead"',
    '("Product Designer" OR "UX Designer") AND ("AI" OR "GenAI" OR "LLM" OR "machine learning")',
    '"Interaction Designer" OR "Senior Interaction Designer" OR "Experience Designer" OR "Senior Experience Designer" OR "Immersive Experience Designer" OR "Multimodal Experience Designer"',
    '"UX Engineer" OR "UX Technologist" OR "Design Technologist" OR "Creative Technologist" OR "UI/UX Engineer" OR "Vibe Prototyper" OR "Vibe Coder"',
    '"AI Product Designer" OR "AI Designer" OR "AI UX Designer" OR "GenAI UX Designer" OR "AI-First Product Designer" OR "AI Experience Designer" OR "AI UX/UI Designer" OR "Machine Experience Designer"',
    '"Senior UI Designer" OR "UI Designer" OR "Senior UI/UX Designer"',
    '"Senior UX Designer" OR "UX Designer" OR "Senior UX/UI Designer" OR "UX/UI Designer" OR "UI/UX Designer"',
    '"Senior Product Designer" OR "Lead Product Designer" OR "Product Designer" OR "Staff Product Designer" OR "Principal Product Designer" OR "Senior Product & UX Designer"',
]

# Prefer USA first, then Canada, then EMEA when sorting / searching.
LINKEDIN_GEO_PRIORITY: list[dict[str, str]] = [
    {"key": "usa", "label": "United States", "geoId": "103644278"},
    {"key": "canada", "label": "Canada", "geoId": "101174742"},
    {"key": "emea", "label": "Europe Middle East Africa", "geoId": "91000000"},
]

# Soft cap per scout run (aim 12-15).
LINKEDIN_SCOUT_SOFT_CAP = 15

# LinkedIn "Past 24 hours" time filter.
LINKEDIN_POSTED_PAST_24H = "r86400"
