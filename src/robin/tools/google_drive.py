import base64
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Type

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

from robin.tools.google_auth import get_credentials


def _drive_service():
    return build("drive", "v3", credentials=get_credentials())


def get_output_folder_id() -> Optional[str]:
    """Return GOOGLE_DRIVE_FOLDER_ID when set (must be a real folder, not a Drive Project)."""
    folder_id = (os.environ.get("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    return folder_id or None


def _file_body(name: str, mime_type: Optional[str] = None) -> dict:
    body: dict = {"name": name}
    if mime_type:
        body["mimeType"] = mime_type
    folder_id = get_output_folder_id()
    if folder_id:
        body["parents"] = [folder_id]
    return body


def ensure_run_subfolder(run_id: Optional[str] = None) -> Optional[str]:
    """Create (or reuse) a dated run subfolder under GOOGLE_DRIVE_FOLDER_ID."""
    parent = get_output_folder_id()
    if not parent:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name = f"Run {stamp}"
    if run_id:
        name = f"Run {stamp} ({run_id[:8]})"
    service = _drive_service()
    existing = (
        service.files()
        .list(
            q=(
                f"name='{name}' and '{parent}' in parents "
                "and mimeType='application/vnd.google-apps.folder' and trashed=false"
            ),
            fields="files(id,name)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    if existing:
        return existing[0]["id"]
    created = (
        service.files()
        .create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent],
            },
            fields="id",
        )
        .execute()
    )
    return created.get("id")


def upload_text_file(
    filename: str,
    content: str,
    *,
    parent_id: Optional[str] = None,
    mime_type: str = "text/markdown",
) -> str:
    """Upload a text/markdown file into the outputs folder (or parent_id). Returns web link."""
    service = _drive_service()
    body: dict = {"name": filename}
    folder_id = parent_id or get_output_folder_id()
    if folder_id:
        body["parents"] = [folder_id]
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type)
    created = (
        service.files()
        .create(body=body, media_body=media, fields="id,webViewLink")
        .execute()
    )
    return created.get("webViewLink") or f"https://drive.google.com/file/d/{created.get('id')}/view"


def move_file_to_output_folder(file_id: str) -> None:
    """Move an existing Drive file into GOOGLE_DRIVE_FOLDER_ID when configured."""
    folder_id = get_output_folder_id()
    if not folder_id or not file_id:
        return
    service = _drive_service()
    meta = service.files().get(fileId=file_id, fields="parents").execute()
    previous = ",".join(meta.get("parents") or [])
    service.files().update(
        fileId=file_id,
        addParents=folder_id,
        removeParents=previous or None,
        fields="id,parents",
    ).execute()


def save_agent_output_to_drive(
    agent_id: Optional[str],
    task_key: Optional[str],
    output_text: str,
    *,
    run_id: Optional[str] = None,
) -> Optional[str]:
    """Persist one agent task output as markdown under today's run subfolder."""
    if not get_output_folder_id():
        return None
    parent = ensure_run_subfolder(run_id)
    safe = re.sub(r"[^\w\-]+", "_", (agent_id or task_key or "agent")).strip("_") or "agent"
    stamp = datetime.now(timezone.utc).strftime("%H%M%S")
    filename = f"{safe}_{stamp}.md"
    body = (
        f"# {agent_id or 'agent'}\n\n"
        f"- Task: `{task_key or ''}`\n"
        f"- Saved: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"---\n\n{output_text}\n"
    )
    try:
        return upload_text_file(filename, body, parent_id=parent)
    except Exception as exc:
        print(f"[drive] failed to save agent output ({safe}): {exc}")
        return None


class GoogleDrivePdfUploadToolInput(BaseModel):
    """Input schema for GoogleDrivePdfUploadTool."""

    pdf_base64: str = Field(
        ...,
        description=(
            "Base64-encoded PDF content, OR the short ref returned by Latex To Pdf Compiler "
            "(e.g. FILE:last_compile.b64). Prefer the FILE: ref to avoid huge tool args."
        ),
    )
    filename: str = Field(
        ...,
        description='The desired filename for the PDF, e.g. "Acme - Senior Product Designer - Resume.pdf".',
    )


_LAST_COMPILE_B64_PATH = Path("dashboard/.cache/last_compile.b64")
_ALLOWED_FILE_REFS = frozenset(
    {
        "last_compile.b64",
        "dashboard/.cache/last_compile.b64",
    }
)
_FILE_REF_RE = re.compile(r"FILE:\s*([^\s`'\"<>]+)", re.IGNORECASE)
_PDF_MAGIC = b"%PDF-"
_MIN_PDF_BYTES = 1000
_WRAP_CHARS = "'\"`"


def _unwrap_pdf_input(value: str) -> str:
    """Strip whitespace and surrounding quotes/backticks the LLM often adds."""
    raw = (value or "").strip()
    while len(raw) >= 2 and raw[0] in _WRAP_CHARS and raw[-1] in _WRAP_CHARS:
        raw = raw[1:-1].strip()
    return raw


def _extract_file_ref(raw: str) -> Optional[str]:
    """Return a FILE: name if one appears anywhere in the (unwrapped) input."""
    match = _FILE_REF_RE.search(raw)
    if not match:
        return None
    name = match.group(1).replace("\\", "/").lstrip("/")
    name = name.rstrip(".,;:)]}")
    return name or None


def _validate_pdf_bytes(pdf_bytes: bytes) -> None:
    """Reject anything that is not a real PDF before it can be uploaded."""
    data = pdf_bytes or b""
    size = len(data)
    if size < _MIN_PDF_BYTES or not data.startswith(_PDF_MAGIC):
        raise ValueError(
            "decoded bytes are not a valid PDF "
            f"(size={size} bytes, magic={data[:8]!r}). "
            "Do not upload. Retry with FILE:last_compile.b64 from Latex To Pdf Compiler."
        )


def _resolve_pdf_base64(pdf_base64: str) -> bytes:
    """Decode Base64 or load the compiler cache FILE: ref.

    FILE: detection is tolerant of wrapping quotes/backticks, extra prose, and
    casing, so a near-correct LLM echo still loads the cached compile instead
    of silently decoding the malformed string itself.
    """
    raw = _unwrap_pdf_input(pdf_base64)
    file_name = _extract_file_ref(raw)
    if file_name is not None:
        # Only allow the known compiler cache file (no path traversal).
        if file_name.lower() not in {item.lower() for item in _ALLOWED_FILE_REFS}:
            raise ValueError(
                f"Unsupported FILE ref: {raw[:80]}. "
                "Use FILE:last_compile.b64 from Latex To Pdf Compiler."
            )
        path = _LAST_COMPILE_B64_PATH
        if not path.is_file():
            raise FileNotFoundError(
                "No cached compile at dashboard/.cache/last_compile.b64. "
                "Run Latex To Pdf Compiler first."
            )
        raw = path.read_text(encoding="utf-8").strip()
    return base64.b64decode(raw)


class GoogleDrivePdfUploadTool(BaseTool):
    """Tool for uploading a Base64-encoded PDF to Google Drive (OAuth user account)."""

    name: str = "Google Drive PDF Upload Tool"
    description: str = (
        "Uploads a Base64-encoded PDF file to Google Drive. "
        "Accepts raw Base64 or the FILE:last_compile.b64 ref from Latex To Pdf Compiler. "
        "When GOOGLE_DRIVE_FOLDER_ID is set, files are placed in that folder. "
        "Returns a public shareable link to the uploaded PDF."
    )
    args_schema: Type[BaseModel] = GoogleDrivePdfUploadToolInput

    def _run(self, pdf_base64: str, filename: str) -> str:
        try:
            pdf_bytes = _resolve_pdf_base64(pdf_base64)
            _validate_pdf_bytes(pdf_bytes)
        except Exception as exc:
            return f"Error: Could not resolve PDF content. Details: {exc}"

        service = _drive_service()
        media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf")

        file = service.files().create(
            body=_file_body(filename),
            media_body=media,
            fields="id",
        ).execute()
        file_id = file.get("id")

        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return f"https://drive.google.com/file/d/{file_id}/view"
