from typing import List, Type

from googleapiclient.discovery import build
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

from jobhunter_ai.tools.google_auth import get_credentials


def _sheets_service():
    return build("sheets", "v4", credentials=get_credentials())


class GoogleSheetsCreateToolInput(BaseModel):
    """Input schema for GoogleSheetsCreateTool."""

    title: str = Field(..., description="Title for the new Google Sheet.")
    headers: List[str] = Field(..., description="Header row values to write to A1:Z1.")


class GoogleSheetsCreateTool(BaseTool):
    """Tool for creating a new Google Sheet and writing its header row."""

    name: str = "Google Sheets Create Tool"
    description: str = (
        "Creates a new Google Sheet with the given title and writes the given headers as the first row. "
        "Returns the shareable edit link to the created spreadsheet."
    )
    args_schema: Type[BaseModel] = GoogleSheetsCreateToolInput

    def _run(self, title: str, headers: List[str]) -> str:
        service = _sheets_service()
        spreadsheet = service.spreadsheets().create(
            body={"properties": {"title": title}}
        ).execute()
        sheet_id = spreadsheet.get("spreadsheetId")

        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="A1:Z1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

        return f"Created: https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


class GoogleSheetsAppendToolInput(BaseModel):
    """Input schema for GoogleSheetsAppendTool."""

    spreadsheet_id: str = Field(..., description="The ID of the spreadsheet to append rows to.")
    rows: List[List[str]] = Field(..., description="Rows of values to append, one list per row.")


class GoogleSheetsAppendTool(BaseTool):
    """Tool for appending rows to an existing Google Sheet."""

    name: str = "Google Sheets Append Tool"
    description: str = (
        "Appends one or more rows of values to an existing Google Sheet, given its spreadsheet ID."
    )
    args_schema: Type[BaseModel] = GoogleSheetsAppendToolInput

    def _run(self, spreadsheet_id: str, rows: List[List[str]]) -> str:
        service = _sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

        return f"Appended {len(rows)} rows to {spreadsheet_id}"


class GoogleSheetsSearchToolInput(BaseModel):
    """Input schema for GoogleSheetsSearchTool."""

    spreadsheet_id: str = Field(..., description="The ID of the spreadsheet to search.")
    column_index: int = Field(..., description="Zero-based column index to check against search_value.")
    search_value: str = Field(..., description="The value to search for in the given column.")


class GoogleSheetsSearchTool(BaseTool):
    """Tool for searching a Google Sheet column for an exact value match."""

    name: str = "Google Sheets Search Tool"
    description: str = (
        "Searches a Google Sheet for a given value in a given column index. "
        "Returns 'FOUND' if a matching row exists, otherwise 'NOT_FOUND'."
    )
    args_schema: Type[BaseModel] = GoogleSheetsSearchToolInput

    def _run(self, spreadsheet_id: str, column_index: int, search_value: str) -> str:
        service = _sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="A:Z",
        ).execute()

        rows = result.get("values", [])
        for row in rows:
            if len(row) > column_index and row[column_index] == search_value:
                return "FOUND"

        return "NOT_FOUND"
