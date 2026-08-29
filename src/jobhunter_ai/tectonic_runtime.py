"""Resolve a local Tectonic binary for LaTeX compilation, downloading it once if needed.

Tectonic (MIT-licensed, https://github.com/tectonic-typesetting/tectonic) is a
self-contained LaTeX engine shipped as a single executable per platform. There
is no PyPI wheel that bundles it, so this mirrors how tools like Playwright
manage browser binaries: check PATH, then a local cache, then download the
matching GitHub Release asset once and reuse it forever. The first compile may
fetch a small TeX-package "bundle" from Tectonic's own CDN (never user data,
only generic package files) and caches it under the same directory for fully
offline compiles after that.
"""

from __future__ import annotations

import io
import json
import platform
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path

_RELEASES_API = (
    "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"
)
_CACHE_DIR = Path.home() / ".cache" / "jobhunter-ai" / "tectonic"
_DOWNLOAD_TIMEOUT = 300
_USER_AGENT = "jobhunter-ai-tectonic-bootstrap"


def _binary_name() -> str:
    return "tectonic.exe" if platform.system() == "Windows" else "tectonic"


def _target_triple() -> tuple[str, str]:
    """Return (release asset target triple, archive extension) for this machine."""
    system = platform.system()
    is_arm = platform.machine().lower() in ("arm64", "aarch64")

    if system == "Windows":
        return "x86_64-pc-windows-msvc", "zip"
    if system == "Darwin":
        return ("aarch64-apple-darwin" if is_arm else "x86_64-apple-darwin"), "tar.gz"
    if system == "Linux":
        return (
            "aarch64-unknown-linux-musl" if is_arm else "x86_64-unknown-linux-musl"
        ), "tar.gz"
    raise RuntimeError(f"Tectonic has no known prebuilt binary for platform {system!r}")


def _cached_binary() -> Path | None:
    candidate = _CACHE_DIR / _binary_name()
    return candidate if candidate.is_file() else None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        return resp.read()


def _extract_binary(archive_bytes: bytes, ext: str, dest: Path) -> None:
    name = dest.name
    if ext == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            member = next(n for n in zf.namelist() if n.endswith(name))
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
        return

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        member = next(
            m for m in tf.getmembers() if m.isfile() and m.name.endswith(name)
        )
        src = tf.extractfile(member)
        if src is None:
            raise RuntimeError("Tectonic archive member could not be read")
        with dest.open("wb") as out:
            shutil.copyfileobj(src, out)


def _download_and_cache() -> Path:
    triple, ext = _target_triple()
    release = json.loads(_fetch(_RELEASES_API).decode("utf-8"))

    asset = next(
        (a for a in release.get("assets", []) if a["name"].endswith(f"{triple}.{ext}")),
        None,
    )
    if asset is None:
        raise RuntimeError(f"No Tectonic release asset found for target {triple!r}")

    archive_bytes = _fetch(asset["browser_download_url"])

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / _binary_name()
    _extract_binary(archive_bytes, ext, target)

    if platform.system() != "Windows":
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return target


def resolve_tectonic() -> Path:
    """Return a path to a working `tectonic` executable, downloading it once if needed."""
    on_path = shutil.which("tectonic")
    if on_path:
        return Path(on_path)

    cached = _cached_binary()
    if cached is not None:
        return cached

    return _download_and_cache()
