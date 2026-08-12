"""Job-search benchmarks.

PUBLISHED holds figures printed by a cited source. Nothing else belongs here.
Derived or estimated values go in DERIVED and must be labelled "estimated" in
the UI -- never presented with a PUBLISHED source string. Prior draft shipped
tech:55 and design:48 as if published; neither figure is printed anywhere --
they were extrapolated from the "191 applicants per hire in tech vs 95
general" ratio. Removed. Every profession falls through to the one figure
that is actually sourced until a real per-profession number exists.
"""

from __future__ import annotations

APPLICATIONS_PER_INTERVIEW = {"general": 42}  # published; no per-profession source exists
DERIVED: dict[str, int] = {}  # populate only with an explicit "estimated" label
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
