from typing import Type

from googleapiclient.discovery import build
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

from robin.tools.google_auth import get_credentials
from robin.tools.google_drive import move_file_to_output_folder


def _docs_service():
    return build("docs", "v1", credentials=get_credentials())


class GoogleDocsCreateToolInput(BaseModel):
    """Input schema for GoogleDocsCreateTool."""

    title: str = Field(..., description="Title for the new Google Doc.")
    content: str = Field(..., description="Text content to insert into the new document.")


class GoogleDocsCreateTool(BaseTool):
    """Tool for creating a new Google Doc with initial text content."""

    name: str = "Google Docs Create Tool"
    description: str = (
        "Creates a new Google Doc with the given title and inserts the given text content. "
        "Returns the shareable edit link to the created document."
    )
    args_schema: Type[BaseModel] = GoogleDocsCreateToolInput

    def _run(self, title: str, content: str) -> str:
        service = _docs_service()
        doc = service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")

        service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": content,
                        }
                    }
                ]
            },
        ).execute()

        try:
            move_file_to_output_folder(doc_id)
        except Exception as exc:
            print(f"[drive] could not move doc into output folder: {exc}")

        return f"Created: https://docs.google.com/document/d/{doc_id}/edit"


class GoogleDocsGetToolInput(BaseModel):
    """Input schema for GoogleDocsGetTool."""

    doc_id: str = Field(..., description="The Google Doc ID to retrieve content from.")


class GoogleDocsGetTool(BaseTool):
    """Tool for retrieving the full plain-text content of a Google Doc."""

    name: str = "Google Docs Get Tool"
    description: str = (
        "Retrieves the full plain-text content of an existing Google Doc, given its document ID."
    )
    args_schema: Type[BaseModel] = GoogleDocsGetToolInput

    def _run(self, doc_id: str) -> str:
        service = _docs_service()
        document = service.documents().get(documentId=doc_id).execute()

        text_parts = []
        for element in document.get("body", {}).get("content", []):
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for run in paragraph.get("elements", []):
                text_run = run.get("textRun")
                if text_run:
                    text_parts.append(text_run.get("content", ""))

        return "".join(text_parts)


class GoogleDocsReplaceToolInput(BaseModel):
    """Input schema for GoogleDocsReplaceTool."""

    doc_id: str = Field(..., description="The Google Doc ID to modify.")
    find_text: str = Field(..., description="The text to search for.")
    replacement_text: str = Field(..., description="The text to replace all matches with.")


class GoogleDocsReplaceTool(BaseTool):
    """Tool for finding and replacing all occurrences of text within a Google Doc."""

    name: str = "Google Docs Replace Tool"
    description: str = (
        "Replaces every occurrence of find_text with replacement_text inside an existing Google Doc, "
        "given its document ID. Used to update a doc in-place with humanized content."
    )
    args_schema: Type[BaseModel] = GoogleDocsReplaceToolInput

    def _run(self, doc_id: str, find_text: str, replacement_text: str) -> str:
        service = _docs_service()
        result = service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": find_text,
                                "matchCase": False,
                            },
                            "replaceText": replacement_text,
                        }
                    }
                ]
            },
        ).execute()

        occurrences = 0
        for reply in result.get("replies", []):
            replace_result = reply.get("replaceAllText")
            if replace_result:
                occurrences = replace_result.get("occurrencesChanged", 0)

        return f"Replaced {occurrences} instances in doc {doc_id}"
