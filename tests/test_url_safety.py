"""host_matches must reject lookalike/substring bypasses, not just
recognize the real host -- CodeQL py/incomplete-url-substring-sanitization."""

from __future__ import annotations

from jobhunter_ai.url_safety import host_matches


def test_matches_exact_host():
    assert host_matches("https://linkedin.com/in/foo", "linkedin.com") is True


def test_matches_subdomain():
    assert host_matches("https://www.linkedin.com/in/foo", "linkedin.com") is True


def test_matches_bare_domain_no_scheme():
    assert host_matches("linkedin.com/in/foo", "linkedin.com") is True


def test_rejects_lookalike_suffix():
    assert host_matches("https://notlinkedin.com.evil.com/x", "linkedin.com") is False


def test_rejects_substring_in_path_or_query():
    assert host_matches("https://evil.com/?x=linkedin.com", "linkedin.com") is False


def test_rejects_userinfo_trick():
    assert host_matches("https://linkedin.com@evil.com/x", "linkedin.com") is False


def test_empty_url_is_false():
    assert host_matches("", "linkedin.com") is False
