"""Gmail outcome detection (SPEC.md §3.2).

Deterministic keyword rules run first and decide almost every message. An LLM is
consulted only when no rule fires at all, and it sees at most 1,200 characters of
body text (Rule 1) - never a full email.

Nothing here auto-advances an application past `replied`. A misclassified
"unfortunately" would otherwise close a live opportunity behind the user's back,
so `interview`, `offer`, and `rejection` all wait for confirmation in the UI.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from robin import gmail_verify, pipeline_store
from robin.db import connect, utc_now
from robin.job_sources.normalize import clean_company

CLASSIFICATIONS = ("rejection", "interview", "offer", "ack", "other")

# SPEC.md §3.2. Checked in this order, and rejection is checked first on
# purpose: rejections routinely contain the word "interview"
# ("thank you for interviewing with us").
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "rejection",
        (
            "unfortunately",
            "not moving forward",
            "other candidates",
            "we regret",
            "will not be moving forward",
            "decided not to proceed",
        ),
    ),
    (
        "offer",
        ("compensation package", "we'd like to welcome", "we would like to welcome", "offer letter"),
    ),
    (
        "interview",
        ("schedule", "interview", "availability", "chat with", "next steps"),
    ),
    (
        "ack",
        ("received your application", "thanks for applying", "thank you for applying"),
    ),
)

# "offer" alone is too loose to stand on its own ("happy to offer feedback"), so
# it only counts next to hiring language.
_OFFER_CONTEXT = re.compile(
    r"\boffer\b[^.\n]{0,80}\b(position|role|job|salary|compensation|employment|start date|join the team)\b"
    r"|\b(position|role|job)\b[^.\n]{0,40}\boffer\b",
    re.I,
)

_MIN_CONFIDENCE = 0.6
_LLM_BODY_LIMIT = 1200  # Rule 1: hard ceiling on what reaches an LLM.

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _hits(blob: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for word in keywords if word in blob)


def classify(subject: str, body: str, *, use_llm: bool = False) -> tuple[str, float]:
    """Return (classification, confidence). ('other', 0.0) when nothing matches."""
    blob = f"{subject or ''}\n{body or ''}".lower()
    if not blob.strip():
        return "other", 0.0

    for label, keywords in _RULES:
        hits = _hits(blob, keywords)
        if label == "offer" and _OFFER_CONTEXT.search(blob):
            hits += 1
        if hits:
            # More independent signals -> more confidence, capped below certainty.
            return label, min(_MIN_CONFIDENCE + 0.15 * (hits - 1), 0.95)

    if use_llm:
        guess = _llm_classify(subject or "", body or "")
        if guess is not None:
            return guess
    return "other", 0.0


def _llm_classify(subject: str, body: str) -> tuple[str, float] | None:
    """Last-resort classification. Sends at most 1,200 characters of body."""
    model = _pick_model()
    if not model:
        return None
    excerpt = (body or "")[:_LLM_BODY_LIMIT]
    prompt = (
        "Classify this email about a job application as exactly one of: "
        "rejection, interview, offer, ack, other. Reply with the single word only.\n\n"
        f"Subject: {subject[:200]}\n\n{excerpt}"
    )
    try:
        import litellm

        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip().lower()
    except Exception as exc:  # noqa: BLE001 - classification is best-effort
        print(f"[outcomes] LLM classification skipped: {exc!r}")
        return None

    for label in CLASSIFICATIONS:
        if label in raw:
            # Never as trusted as a keyword match, and never above the bar that
            # would let it drive an automatic status change on its own.
            return (label, 0.6) if label != "other" else ("other", 0.0)
    return None


def _pick_model() -> str:
    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        return "gemini/gemini-2.5-flash"
    if (os.environ.get("GROQ_API_KEY") or "").strip():
        return "groq/openai/gpt-oss-20b"
    return ""


# ── Matching inbound mail to applications ──────────────────────────────────


def _domain(value: str) -> str:
    host = (urlparse(value or "").netloc or "").lower()
    if not host and "@" in (value or ""):
        host = value.rsplit("@", 1)[-1].strip(" >").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _registrable(host: str) -> str:
    """Best-effort eTLD+1 so `jobs.figma.com` matches `figma.com`."""
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) < 2:
        return host or ""
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _company_tokens(company: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(clean_company(company)) if len(t) > 2}


def match_application(
    from_addr: str,
    subject: str,
    applications: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match a message to an application (SPEC.md Step 6 priority order).

    1. sender domain == the domain of job.url
    2. sender domain == a normalized form of job.company
    3. company name in the subject line
    """
    sender = _registrable(_domain(from_addr))
    subject_blob = (subject or "").lower()

    if sender:
        for app in applications:
            if sender and sender == _registrable(_domain(app.get("url") or "")):
                return app

        sender_stem = sender.split(".")[0]
        for app in applications:
            if sender_stem and sender_stem in _company_tokens(app.get("company") or ""):
                return app

    for app in applications:
        tokens = _company_tokens(app.get("company") or "")
        if tokens and all(token in subject_blob for token in tokens):
            return app
    return None


def _header(headers: list[dict[str, str]], name: str) -> str:
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return header.get("value") or ""
    return ""


def _received_at(message: dict[str, Any]) -> str:
    raw = message.get("internalDate")
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return utc_now()


def _open_applications(conn) -> list[dict[str, Any]]:
    """Applications a reply could plausibly belong to."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT a.id, a.status, j.company, j.url
            FROM application a JOIN job j ON j.id = a.job_id
            WHERE a.status IN ('applied', 'replied', 'interview', 'offer')
            ORDER BY a.updated_at DESC
            """
        )
    ]


def scan_inbox(days: int = 30, *, service=None, conn=None) -> dict[str, Any]:
    """Scan recent mail, store matched replies, return a summary.

    Idempotent: `inbound_message.gmail_msg_id` is UNIQUE and inserts ignore
    conflicts, so re-scanning the same window adds nothing.
    """
    summary: dict[str, Any] = {
        "ok": True,
        "scanned": 0,
        "matched": 0,
        "new": 0,
        "advanced": 0,
        "classifications": {},
        "messages": [],
    }

    service = service or gmail_verify._get_gmail_service()
    if service is None:
        summary["ok"] = False
        summary["error"] = "Gmail is not connected. Connect it in Settings first."
        return summary

    own = conn is None
    conn = conn or connect()
    try:
        applications = _open_applications(conn)
        if not applications:
            summary["error"] = "No applied applications yet - nothing to match against."
            return summary

        after = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%Y/%m/%d")
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=f"after:{after} -category:promotions", maxResults=200)
            .execute()
        )
        use_llm = bool(_pick_model())

        for stub in listing.get("messages", []) or []:
            message = (
                service.users()
                .messages()
                .get(userId="me", id=stub["id"], format="full")
                .execute()
            )
            summary["scanned"] += 1
            payload = message.get("payload") or {}
            headers = payload.get("headers") or []
            from_addr = _header(headers, "From")
            subject = _header(headers, "Subject")

            app = match_application(from_addr, subject, applications)
            if app is None:
                continue
            summary["matched"] += 1

            body = gmail_verify._message_body(payload)
            classification, confidence = classify(subject, body, use_llm=use_llm)
            summary["classifications"][classification] = (
                summary["classifications"].get(classification, 0) + 1
            )

            with conn:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO inbound_message
                        (application_id, gmail_msg_id, from_addr, subject, snippet,
                         received_at, classification, confidence, confirmed_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        app["id"],
                        message.get("id"),
                        from_addr,
                        subject,
                        (message.get("snippet") or "")[:500],
                        _received_at(message),
                        classification,
                        confidence,
                        utc_now(),
                    ),
                )
                is_new = cur.rowcount > 0

            if not is_new:
                continue
            summary["new"] += 1
            summary["messages"].append(
                {
                    "application_id": app["id"],
                    "company": app.get("company"),
                    "from": from_addr,
                    "subject": subject,
                    "classification": classification,
                    "confidence": confidence,
                    "needs_confirmation": classification in {"interview", "offer", "rejection"},
                }
            )

            # `replied` is the only automatic advance (SPEC.md §3.2). Interview,
            # offer, and rejection wait for the user.
            if classification != "other" and app["status"] == "applied":
                pipeline_store.set_status(
                    app["id"],
                    "replied",
                    "gmail",
                    f"{classification} ({confidence:.2f}): {subject[:120]}",
                    conn=conn,
                )
                app["status"] = "replied"
                summary["advanced"] += 1

        return summary
    finally:
        if own:
            conn.close()


def confirm(inbound_message_id: int, classification: str, *, conn=None) -> dict[str, Any]:
    """Record the user's verdict and advance the application accordingly."""
    classification = (classification or "").strip()
    if classification not in CLASSIFICATIONS:
        raise ValueError(
            f"invalid classification {classification!r}; allowed: {', '.join(CLASSIFICATIONS)}"
        )

    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT * FROM inbound_message WHERE id = ?", (inbound_message_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no inbound message with id {inbound_message_id}")

        with conn:
            conn.execute(
                "UPDATE inbound_message SET classification = ?, confirmed_by = 'user' WHERE id = ?",
                (classification, inbound_message_id),
            )

        status = {"interview": "interview", "offer": "offer", "rejection": "rejected"}.get(
            classification
        )
        application_id = row["application_id"]
        if status and application_id:
            pipeline_store.set_status(
                application_id,
                status,
                "user",
                f"confirmed from email: {row['subject'][:120]}",
                conn=conn,
            )
        return {
            "ok": True,
            "inbound_message_id": inbound_message_id,
            "classification": classification,
            "application_id": application_id,
            "status": status,
        }
    finally:
        if own:
            conn.close()
