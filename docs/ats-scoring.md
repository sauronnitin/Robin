# ATS match scoring — what it is and where the model came from

`src/jobhunter_ai/ats_score.py` scores a resume against one job posting, 0–100.

## Two scores, two questions

| | Question | Who computes it | Range seen |
|---|---|---|---|
| **Fit score** | Is this job worth applying to? | The Score agent (LLM) | 25–75 before recalibration |
| **ATS score** | Would a keyword matcher rank this resume for this posting? | `ats_score.py` (deterministic) | 44–96 on real postings |

They move independently, and that is the point. A dream job you are badly
keyword-matched for is high fit / low ATS — exactly the case tailoring exists to
fix. A generic role you happen to match on paper is the reverse, and no amount
of tailoring makes it worth applying to.

**The ATS score is deliberately not an LLM call.** It gates a retry loop, so it
has to be stable and cheap; and an agent asked to grade its own resume grades
generously.

## The model

Built from how real matchers are documented to behave:

1. **Hard skills dominate.** Concrete, checkable nouns — tools, methods,
   platforms, certifications — are what an engine can verify. Soft phrasing
   ("team player", "detail oriented") scores nothing; `_SOFT_NOISE` drops it.
2. **Required beats preferred.** Terms above a "nice to have" heading weigh
   full; terms below it weigh half. Everything before any such heading counts as
   required, which is the conservative reading — over-weighting a preference
   costs less than ignoring a requirement.
3. **Repetition signals importance, with diminishing returns.**
   `1 + log₃(occurrences)`, so a word repeated ten times cannot swamp a posting.
4. **Placement matters.** Summary and Skills weigh 1.0, Experience 0.9,
   anything else 0.6. An unrecognised heading (Awards, Volunteer) resets the
   bucket to "other" — otherwise keywords at the bottom of a resume were being
   credited as though they sat in Skills.
5. **Spread beats repetition.** Covering a term in two or more sections earns a
   1.1× bonus; the final score is clamped to 100 so stuffing cannot buy more
   than it is worth.

**Closed vocabulary, on purpose.** Scoring every content word a posting uses was
tried and abandoned: it produced 400+ "terms" per posting (mostly noise — "here",
"people", the company's own name) and destroyed the signal, scoring a
graphic-design role identically to a product-design one. The vocabulary in
`default_vocabulary()` is the thing to extend when a real posting's terms are
being missed.

## Thresholds

- `MINIMUM_SCORE = 65` — the tailor guardrail refuses to ship below this and
  hands back the specific missing keywords.
- `TARGET_SCORE = 70` — the band where callbacks measurably improve.
- Qualifying fit score is **45** (`_MIN_QUALIFYING_SCORE` in `crew.py`). Below
  that a job still costs a full tailor + compile + apply pass, for a role the
  candidate is a weak match for.

## The honesty constraint

The tailoring task may only surface skills the candidate **already has**, in the
posting's vocabulary, placed where a parser reads them. It may not invent
employment, tools, dates, or metrics. When a posting wants something genuinely
absent, the correct outcome is a lower score and a missing keyword — not a false
claim.

This is why the guardrail retries **once** rather than looping: a second pass
fixes an under-keyworded resume, a third just argues with a posting the
candidate does not match. The Lemon.io Senior Graphic Designer posting is the
worked example — its gaps are `react, node, python, vue, angular`, and the right
answer is to leave them missing and let the 45-point fit floor drop the job.

## Sources

- [Resume Matching Explained: How ATS Systems Actually Score You Against a Job](https://resumeoptimizerpro.com/blog/resume-matching-explained)
- [ATS Score Explained: How Resume Scores Are Calculated](https://resumeoptimizerpro.com/blog/ats-resume-score-guide)
- [ATS Resume Scoring Criteria: The 9-Point Checklist](https://www.atsresumeai.com/blog/resume-ats-score)
- [Resume Keywords Guide 2025 — ATS Keywords & Skill Matching](https://resumeshowdown.com/resume-keywords-guide)
- [ATS Score Explained: What Match Scores Mean](https://www.atschecker.ai/guides/ats-score-explained)
- [The Science Behind Resume Scoring — Research & Methodology](https://www.resumeseopro.com/science)
- [Resume Screening using TF-IDF (IJARCCE)](https://ijarcce.com/papers/resume-screening-using-tf-idf/)
- [Job Posting-Enriched Knowledge Graph for Skills-based Matching (arXiv)](https://arxiv.org/pdf/2109.02554)
