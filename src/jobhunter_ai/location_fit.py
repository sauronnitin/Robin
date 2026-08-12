"""Where the candidate can actually work, and how a posting compares.

Universal rule: the candidate's own country is the default and the priority.
That country comes from their profile, so a tester in India gets India-first
exactly the way a user in the US gets US-first. Nothing here hardcodes a
country - "US" only wins for this user because their profile says so.

Bands, best first:

- **home**   - in the candidate's country. Right to work is already settled.
- **remote** - remote with no country restriction, or explicitly open to theirs.
- **unknown**- the posting does not say. Worth a look, not worth a preference.
- **elsewhere** - another country. Usually means visa sponsorship and relocation,
  which is a different decision from "is this a good job".
"""

from __future__ import annotations

import re
from typing import Any

BANDS: tuple[str, ...] = ("home", "remote", "unknown", "elsewhere")

# Points contributed to the fit score. Location is one input among several -
# a great job in the wrong country should still outrank a poor local one.
BAND_POINTS: dict[str, int] = {
    "home": 15,
    "remote": 12,
    "unknown": 7,
    "elsewhere": 0,
}

BAND_LABELS: dict[str, str] = {
    "home": "In country",
    "remote": "Remote",
    "unknown": "Location unclear",
    "elsewhere": "Other country",
}

# Names, codes, and enough regional detail to recognise a posting that never
# spells the country out. Extend a country's list when its postings are being
# read as "unknown".
COUNTRY_HINTS: dict[str, list[str]] = {
    # "us" is here because postings write "Remote - US" constantly. It is only
    # ever matched against a location field, never against prose, so the
    # pronoun is not a realistic collision.
    "US": [
        "united states", "usa", "us", "u.s.", "u.s.a", "america", "stateside",
        "new york", "san francisco", "bay area", "los angeles", "seattle",
        "austin", "boston", "chicago", "denver", "atlanta", "miami", "portland",
        "san diego", "washington dc", "nyc", "sf", "silicon valley",
        "california", "texas", "florida", "colorado", "massachusetts",
        "washington state", "illinois", "georgia", "oregon", "utah", "arizona",
        "north carolina", "new jersey", "pennsylvania", "virginia", "michigan",
        "minnesota", "ohio",
    ],
    "IN": [
        "india", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi",
        "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "noida",
        "kolkata", "ahmedabad", "jaipur", "kerala", "karnataka", "maharashtra",
        "tamil nadu", "telangana",
    ],
    "GB": [
        "united kingdom", "uk", "england", "scotland", "wales", "london",
        "manchester", "bristol", "edinburgh", "birmingham", "leeds", "glasgow",
    ],
    "CA": [
        "canada", "toronto", "vancouver", "montreal", "ottawa", "calgary",
        "ontario", "quebec", "british columbia", "alberta",
    ],
    "DE": ["germany", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne"],
    "AU": ["australia", "sydney", "melbourne", "brisbane", "perth", "canberra"],
    "IE": ["ireland", "dublin", "cork"],
    "NL": ["netherlands", "amsterdam", "rotterdam", "utrecht", "the hague"],
    "FR": ["france", "paris", "lyon", "marseille", "toulouse"],
    "ES": ["spain", "madrid", "barcelona", "valencia"],
    "PL": ["poland", "warsaw", "krakow", "kraków", "wroclaw"],
    "BR": ["brazil", "brasil", "sao paulo", "são paulo", "rio de janeiro"],
    "SG": ["singapore"],
    "AE": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "MX": ["mexico", "méxico", "mexico city", "guadalajara"],
    "AR": ["argentina", "buenos aires"],
    "CO": ["colombia", "bogota", "bogotá", "medellin", "medellín"],
    "PH": ["philippines", "manila", "cebu"],
    "NZ": ["new zealand", "auckland", "wellington"],
    "JP": ["japan", "tokyo", "osaka"],
}

# Global remote: open to anyone, so it does not belong to a country.
_ANYWHERE_RE = re.compile(
    r"\b(anywhere|worldwide|world[- ]wide|global(?:ly)?|any country|"
    r"location[- ]independent|fully distributed)\b",
    re.I,
)
_REMOTE_RE = re.compile(r"\b(remote|distributed|work from home|wfh)\b", re.I)

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").replace(",", " , ").lower()).strip()


def home_country(profile: dict[str, Any] | None = None) -> str:
    """The candidate's country code, from their own profile.

    Defaults to US only as a last resort when the profile says nothing - and
    that default is a guess, which callers can see via `is_guess`.
    """
    profile = profile if profile is not None else {}
    blob = _normalize(
        " ".join(
            str(profile.get(key) or "")
            for key in ("country", "state", "city", "address", "address2")
        )
    )
    if blob:
        for code, hints in COUNTRY_HINTS.items():
            for hint in hints:
                if re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", blob):
                    return code
    return "US"


def countries_in(text: str) -> set[str]:
    """Every country a location string mentions."""
    blob = _normalize(text)
    if not blob:
        return set()
    found: set[str] = set()
    for code, hints in COUNTRY_HINTS.items():
        for hint in hints:
            if re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", blob):
                found.add(code)
                break
    return found


def classify(location: str, home: str = "US", work_mode: str = "") -> str:
    """Band one posting's location against the candidate's country."""
    blob = f"{location or ''} {work_mode or ''}".strip()
    if not blob:
        return "unknown"

    mentioned = countries_in(blob)
    if home in mentioned:
        # "Remote - US" and "New York" both settle right-to-work the same way.
        return "home"

    is_remote = bool(_REMOTE_RE.search(blob))
    if mentioned:
        # Remote, but pinned to a country that is not theirs: still elsewhere,
        # because the restriction is what decides eligibility.
        return "elsewhere"

    if is_remote or _ANYWHERE_RE.search(blob):
        return "remote"
    return "unknown"


def points(band: str) -> int:
    return BAND_POINTS.get(band, 0)


def label(band: str) -> str:
    return BAND_LABELS.get(band, band)


def sort_key(band: str) -> int:
    """Lower sorts first: home, then remote, then unknown, then elsewhere."""
    try:
        return BANDS.index(band)
    except ValueError:
        return len(BANDS)
