"""_probe_url must not let /api/jobs/scan-fix's raw request body reach
internal/private network targets -- CodeQL py/full-ssrf, alert #7."""

from __future__ import annotations

from jobhunter_ai.ats_jobs import _is_public_host, _PROBE_ALLOWED_HOSTS, _probe_url


def test_public_ip_is_allowed() -> None:
    # A well-known public IP, classified without any real network I/O.
    assert _is_public_host("8.8.8.8") is True


def test_loopback_is_blocked() -> None:
    assert _is_public_host("127.0.0.1") is False


def test_private_network_is_blocked() -> None:
    assert _is_public_host("10.0.0.5") is False


def test_cloud_metadata_link_local_is_blocked() -> None:
    # 169.254.169.254 is the AWS/GCP/Azure instance-metadata endpoint --
    # the canonical real-world SSRF target this check exists to stop.
    assert _is_public_host("169.254.169.254") is False


def test_unresolvable_host_is_blocked() -> None:
    assert _is_public_host("this-host-does-not-resolve.invalid") is False


def test_probe_url_rejects_private_target_without_making_a_request() -> None:
    ok, detail = _probe_url("http://127.0.0.1:8000/admin")
    assert ok is False
    assert detail == "blocked host"


def test_probe_url_still_rejects_missing_url() -> None:
    ok, detail = _probe_url("")
    assert ok is False
    assert detail == "missing url"


def test_probe_url_rejects_a_public_host_not_on_the_job_board_allowlist() -> None:
    # example.com resolves to a real public IP -- would pass _is_public_host
    # alone -- but it's not one of _HOST_SOURCE's known job-board hosts, so
    # the explicit allowlist check must still refuse it.
    assert "example.com" not in _PROBE_ALLOWED_HOSTS
    ok, detail = _probe_url("https://example.com/x")
    assert ok is False
    assert detail == "blocked host"
