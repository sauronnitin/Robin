"""Published job-search benchmarks. Every number here ships with its source
string, which the UI displays beside the metric."""

from __future__ import annotations

APPLICATIONS_PER_INTERVIEW = {"general": 42, "tech": 55, "design": 48}
INTERVIEW_RATE = 0.03
INTERVIEW_TO_HIRE = 0.27
APPLICANTS_PER_HIRE = {"general": 95, "tech": 191, "healthcare": 47}
NO_RESPONSE_RATE_LOW = 0.48  # Criteria Corp 2025
NO_RESPONSE_RATE_HIGH = 0.75  # Human Capital Institute
ATS_CALLBACK_OPTIMIZED = 0.117
ATS_CALLBACK_GENERIC = 0.042
ATS_KEYWORD_THRESHOLD = 0.70  # inflection point where callbacks jump
TAILORED_MULTIPLIER = 2.5

SOURCES = {
    "applications_per_interview": "ResuTrack / PitchHired 2026",
    "no_response": "Criteria Corp 2025; Human Capital Institute",
    "ats_callback": "15,000-application study, 2024",
}

# Resume-derived role families → benchmark profession keys.
_FAMILY_TO_PROFESSION = {
    "product_design": "design",
    "graphic_design": "design",
    "ux_research": "design",
    "software_engineering": "tech",
    "product_management": "general",
}


def profession_from_family(family: str | None) -> str:
    if not family:
        return "general"
    return _FAMILY_TO_PROFESSION.get(str(family).lower(), "general")


def applications_per_interview(profession: str | None) -> int:
    return APPLICATIONS_PER_INTERVIEW.get(
        (profession or "").lower(), APPLICATIONS_PER_INTERVIEW["general"]
    )


def applicants_per_hire(profession: str | None) -> int:
    return APPLICANTS_PER_HIRE.get(
        (profession or "").lower(), APPLICANTS_PER_HIRE["general"]
    )
