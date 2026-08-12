"""Deterministic resume-to-posting match score.

This is NOT the fit score. Fit answers "is this job worth applying to"; this
answers "would a keyword-matching ATS rank this resume for this posting". They
move independently: a dream job you are badly keyword-matched for scores high on
fit and low here, which is exactly the case tailoring exists to fix.

Modelled on how real matchers behave (see docs/ats-scoring.md for sources):

- Hard skills dominate. Concrete, checkable nouns - tools, methods,
  certifications - outweigh soft phrasing an engine cannot verify.
- Required beats preferred. Terms under "Requirements"/"Must have" are gates;
  "Nice to have" terms are differentiators, so they carry roughly half.
- Repetition in the posting signals importance, with diminishing returns.
- Placement in the resume matters. Summary, skills, and recent experience count
  for more than the same word buried at the bottom.
- Covering a term in two or more sections is the strongest single signal, so it
  earns a bounded bonus rather than an unbounded one.

No LLM is involved. The score has to be stable enough to gate a retry loop, and
an LLM asked to grade its own output grades generously.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# 70%+ is the band where callbacks measurably improve; 65 is the floor we refuse
# to ship below, and the tailor is told to climb as far past it as it honestly can.
TARGET_SCORE = 70.0
MINIMUM_SCORE = 65.0

_REQUIRED_HEADINGS = re.compile(
    r"\b(requirements?|qualifications?|must[- ]haves?|what you.{0,12}(need|bring)|"
    r"we.{0,12}looking for|minimum|required)\b",
    re.I,
)
_PREFERRED_HEADINGS = re.compile(
    r"\b(nice[- ]to[- ]have|preferred|bonus|plus|desirable|good to have|"
    r"even better|extra credit)\b",
    re.I,
)

# Sections of a resume, in the order an ATS weights them.
_PLACEMENT_WEIGHTS: dict[str, float] = {
    "summary": 1.0,
    "skills": 1.0,
    "experience": 0.9,
    "other": 0.6,
}

# Covering a term in two or more sections at once.
_SPREAD_BONUS = 1.1

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("summary", re.compile(r"\b(summary|profile|objective|about)\b", re.I)),
    ("skills", re.compile(r"\b(skills?|technologies|tools|competenc)\w*\b", re.I)),
    ("experience", re.compile(r"\b(experience|employment|work history|projects?)\b", re.I)),
)

# Words that are never a meaningful ATS keyword on their own.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be been being by for from has have how in into is it its
    of on or our that the their they this to was were what when where which who
    will with you your we us able about across all also any both can could day
    each etc every from get give great help high including like look made make
    many may more most much need new non not now off one other over own per plus
    role same see should since some such team teams than them then there these
    those through time under up use used using very via want way well while work
    working years year job company please apply applicant candidate position
    opportunity responsibilities requirements qualifications benefits salary
    experience skills strong excellent good ability including ensure drive own
    across within between during first best right full part time remote hybrid
    onsite office based level senior junior mid staff principal lead manager
    director head vice president
    """.split()
)

# Generic phrases that read as skills but tell a matcher nothing.
_SOFT_NOISE: frozenset[str] = frozenset(
    {
        "team player", "hard worker", "self starter", "detail oriented",
        "fast paced", "cross functional", "problem solving", "communication skills",
        "attention to detail", "great communicator", "strong communication",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{1,}")
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9+#.]+")


@dataclass
class Term:
    """One keyword the posting asks for."""

    text: str
    weight: float
    required: bool
    occurrences: int


@dataclass
class MatchResult:
    score: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    detail: dict[str, float] = field(default_factory=dict)

    def gap_summary(self, limit: int = 12) -> str:
        """The highest-value missing terms, for telling an agent what to add."""
        return ", ".join(self.missing[:limit])


def strip_latex(source: str) -> str:
    """Readable text out of a LaTeX resume - what a parser would extract."""
    text = source or ""
    text = re.sub(r"(?m)^\s*%.*$", " ", text)               # comments
    text = re.sub(r"\\(?:usepackage|documentclass)\[[^\]]*\]\{[^}]*\}", " ", text)
    text = re.sub(r"\\(?:usepackage|documentclass)\{[^}]*\}", " ", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)  # keep link text
    text = re.sub(r"\\section\*?\{([^}]*)\}", r"\n\n\1\n", text)
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:textit|emph|small|item|underline)\b", " ", text)
    text = re.sub(r"\\begin\{[^}]*\}(\[[^\]]*\])?", " ", text)
    text = re.sub(r"\\end\{[^}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?", " ", text)   # any other macro
    text = text.replace("{", " ").replace("}", " ").replace("~", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _phrases(text: str) -> set[str]:
    """Unigrams and bigrams worth treating as keywords."""
    tokens = [t.lower().strip(".") for t in _TOKEN_RE.findall(text or "")]
    tokens = [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]

    found: set[str] = set(tokens)
    for first, second in zip(tokens, tokens[1:]):
        bigram = f"{first} {second}"
        if bigram not in _SOFT_NOISE:
            found.add(bigram)
    return found


def _split_requirement_bands(posting: str) -> tuple[str, str]:
    """Split a posting into (required text, preferred text).

    Everything before a "nice to have" heading counts as required, which is the
    conservative reading: over-weighting a term the posting merely prefers costs
    less than ignoring one it demands.
    """
    lines = (posting or "").splitlines()
    required: list[str] = []
    preferred: list[str] = []
    bucket = required

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _PREFERRED_HEADINGS.search(stripped) and len(stripped) < 90:
            bucket = preferred
            continue
        if _REQUIRED_HEADINGS.search(stripped) and len(stripped) < 90:
            bucket = required
            continue
        bucket.append(stripped)

    return "\n".join(required), "\n".join(preferred)


def extract_terms(posting: str, vocabulary: set[str] | None = None) -> list[Term]:
    """The keywords a posting is actually asking for, weighted by importance."""
    required_text, preferred_text = _split_requirement_bands(posting)
    known = vocabulary if vocabulary is not None else default_vocabulary()

    terms: dict[str, Term] = {}
    for text, required in ((required_text, True), (preferred_text, False)):
        haystack = _normalize(text)
        if not haystack:
            continue
        for phrase in _phrases(text):
            occurrences = haystack.count(phrase)
            if occurrences == 0:
                continue
            # Closed vocabulary on purpose. Scoring every content word a posting
            # uses was tried and destroys the signal: 400+ "terms" per posting,
            # mostly noise ("here", "people", the company's own name), and a
            # graphic-design role scored the same as a product-design one.
            if phrase not in known:
                continue
            # Repetition signals importance, with diminishing returns so one
            # word repeated ten times cannot dominate the whole posting.
            base = 1.0 + math.log(occurrences, 3)
            weight = base * (1.0 if required else 0.5)
            existing = terms.get(phrase)
            if existing is None or weight > existing.weight:
                terms[phrase] = Term(phrase, weight, required, occurrences)

    # A bigram subsumes its parts: "design systems" already covers "systems".
    bigrams = {t for t in terms if " " in t}
    for bigram in bigrams:
        for part in bigram.split():
            if part in terms and not terms[part].required:
                terms.pop(part, None)

    return sorted(terms.values(), key=lambda t: t.weight, reverse=True)


_HEADING_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z &/'-]{2,38}$")


def split_sections(resume_text: str) -> dict[str, str]:
    """Bucket resume text by section so placement can be weighted."""
    sections: dict[str, list[str]] = {key: [] for key in _PLACEMENT_WEIGHTS}
    current = "other"

    for line in (resume_text or "").splitlines():
        stripped = line.strip()
        # A heading we do not recognise (Awards, Volunteer, Publications) must
        # reset the bucket. Letting it inherit the previous section credited
        # keywords at the bottom of a resume as though they sat in Skills.
        if stripped and _HEADING_LINE_RE.match(stripped) and len(stripped.split()) <= 3:
            current = "other"
            for name, pattern in _SECTION_PATTERNS:
                if pattern.search(stripped):
                    current = name
                    break
        sections[current].append(stripped)

    return {name: "\n".join(lines) for name, lines in sections.items()}


def score(posting: str, resume_text: str, vocabulary: set[str] | None = None) -> MatchResult:
    """Score a resume against a posting, 0-100, with the gap that explains it."""
    terms = extract_terms(posting, vocabulary)
    if not terms:
        # Nothing recognisable to match against: report neutral rather than a
        # confident zero, which would trigger endless tailoring for no reason.
        return MatchResult(score=0.0, detail={"terms": 0})

    sections = split_sections(resume_text)
    normalized = {name: _normalize(text) for name, text in sections.items()}
    whole = _normalize(resume_text)

    total = sum(term.weight for term in terms)
    earned = 0.0
    matched: list[str] = []
    missing: list[str] = []

    for term in terms:
        hits = [name for name, text in normalized.items() if term.text in text]
        if not hits and term.text not in whole:
            missing.append(term.text)
            continue

        placement = max((_PLACEMENT_WEIGHTS[name] for name in hits), default=0.6)
        # Two or more sections is the strongest signal a matcher reads. The
        # bonus is deliberately small and the final score is clamped to 100, so
        # keyword stuffing cannot buy more than it is worth.
        spread = _SPREAD_BONUS if len({n for n in hits if n != "other"}) >= 2 else 1.0
        earned += term.weight * placement * spread
        matched.append(term.text)

    percent = round(min(100.0, 100.0 * earned / total), 1) if total else 0.0
    return MatchResult(
        score=percent,
        matched=matched,
        missing=missing,
        detail={
            "terms": float(len(terms)),
            "required_terms": float(sum(1 for t in terms if t.required)),
            "matched": float(len(matched)),
            "weight_total": round(total, 2),
            "weight_earned": round(earned, 2),
        },
    )


def score_latex(posting: str, resume_latex: str, vocabulary: set[str] | None = None) -> MatchResult:
    """score(), for a resume that is still LaTeX source."""
    return score(posting, strip_latex(resume_latex), vocabulary)


_VOCAB_CACHE: set[str] | None = None


def default_vocabulary() -> set[str]:
    """Keywords worth matching on.

    Deliberately a closed vocabulary: matching every shared word between two
    documents rewards filler, and the research is consistent that concrete
    skills are what actually move an ATS score.
    """
    global _VOCAB_CACHE
    if _VOCAB_CACHE is not None:
        return _VOCAB_CACHE

    raw = """
    figma sketch adobe xd invision framer principle protopie rive lottie
    after effects photoshop illustrator indesign blender cinema 4d spline
    webflow miro figjam notion linear jira confluence storybook zeplin abstract

    design systems component library style guide design tokens atomic design
    wireframing wireframes prototyping high-fidelity low-fidelity mockups
    interaction design motion design micro-interactions animation
    visual design typography color theory layout grid systems iconography
    information architecture user flows journey mapping service design
    ux design ui design product design experience design digital product
    responsive design mobile design ios android web design native app
    accessibility wcag aria inclusive design usability

    user research user interviews usability testing a/b testing ab testing
    surveys personas empathy mapping affinity mapping card sorting
    heuristic evaluation competitive analysis contextual inquiry
    quantitative qualitative analytics data-driven metrics kpi
    discovery validation experimentation

    design thinking human-centered user-centered lean ux agile scrum kanban
    sprint roadmap prioritization stakeholder cross-functional collaboration
    product strategy product management go-to-market

    html css javascript typescript react next.js vue angular tailwind
    node python sql git github version control api rest graphql
    three.js webgl canvas svg animation library

    ai machine learning llm prompt engineering generative ai copilot
    claude cursor mcp automation workflow

    saas b2b b2c enterprise marketplace fintech healthtech edtech ecommerce
    e-commerce platform mobile app dashboard onboarding growth retention
    """
    vocabulary = set()
    for line in raw.strip().splitlines():
        for chunk in line.split(","):
            phrase = " ".join(chunk.split()).lower().strip()
            if phrase:
                vocabulary.add(phrase)
    # The block above is whitespace-separated within lines; split those too.
    for phrase in list(vocabulary):
        parts = phrase.split()
        if len(parts) > 2:
            vocabulary.discard(phrase)
            vocabulary.update(parts)
            for first, second in zip(parts, parts[1:]):
                vocabulary.add(f"{first} {second}")

    _VOCAB_CACHE = {v for v in vocabulary if v and v not in _STOPWORDS}
    return _VOCAB_CACHE
