from __future__ import annotations

import json, logging, re
from datetime import date
from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

FIELDS = (
    "patient_id",     # Column A
    "patient_name",   # Column B
    "patient_age",    # Column C
    "issue_date",     # Column D
    "department",     # Column E
    "doctor_name",    # Column F
    "medication",     # Column G
    "quantity",       # Column H
    "dosage_days",    # Column I
)

SheetRow = list[str]
RowOffsets = list[int]

BATCH_MARKER_FORMAT = {
    "textFormat": {"bold": True},
    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
}

UNFLAGGED_MEDICATION_FORMAT = {
    "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
    "textFormat": {
        "foregroundColor": {"red": 0.45, "green": 0.45, "blue": 0.45},
    },
}


def get_gspread_client(creds_path: str | Path) -> gspread.Client:
    """Create an authenticated Google Sheets client."""
    if not creds_path:
        raise ValueError("Google service-account credential.json is required")
    return gspread.service_account(filename=str(creds_path))


def open_worksheet(
    client: gspread.Client,
    sheet_id: str,
    worksheet_name: str = "Sheet1",
) -> gspread.Worksheet:
    """Open a worksheet, creating a named worksheet only when it is missing."""
    spreadsheet = client.open_by_key(sheet_id)
    if worksheet_name == "Sheet1":
        return spreadsheet.sheet1

    try:
        return spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        logging.info("Worksheet '%s' not found; creating it", worksheet_name)
        return spreadsheet.add_worksheet(
            title=worksheet_name,
            rows=1000,
            cols=len(FIELDS),
        )


class PrescriptionSheetUploader:
    """Write prescription rows to one worksheet and archive uploaded files."""

    # Constructor
    def __init__(self, worksheet: gspread.Worksheet) -> None:
        self.worksheet = worksheet


    def upload_batch_marker(self) -> None:
        """Write one dated row to mark the start of a new processing batch."""
        today = date.today().strftime("%d-%m-%Y")
        row = [""] * len(FIELDS)
        row[0] = f"Batch {today}"

        first_row, _ = self._write_rows([row], "batch marker")
        target_range = f"A{first_row}:{rowcol_to_a1(first_row, len(FIELDS))}"
        self.worksheet.format(target_range, BATCH_MARKER_FORMAT)


    def upload_prescription(self, image_path: Path, json_path: Path) -> None:
        """Upload one OCR result and archive its JSON/image pair on success"""
        rows, patient_name, unflagged_rows = _parse_prescription_json(json_path)
        if not rows:
            logging.info("No rows found in %s", json_path.name)
            return

        # Write rows to worksheet
        first_row, _ = self._write_rows(rows, json_path.name)
        self._format_unflagged_rows(first_row, unflagged_rows)

        # If _write_rows() succeeded, archive the JSON and its source image
        self._archive_files(json_path, image_path, patient_name)


    def _write_rows(self, rows: list[SheetRow], source_name: str) -> tuple[int, int]:
        """Write a batch to the next free row, always beginning in column A."""
        first_row = len(self.worksheet.get_all_values()) + 1
        last_row = first_row + len(rows) - 1
        target_range = f"A{first_row}:{rowcol_to_a1(last_row, len(FIELDS))}"

        response = self.worksheet.update(rows, range_name=target_range, value_input_option="USER_ENTERED")

        updated_range = response.get("updatedRange", target_range)
        logging.info("Wrote %d rows from %s to %s", len(rows), source_name, updated_range)
        return first_row, last_row


    def _format_unflagged_rows(self, first_row: int, row_offsets: RowOffsets) -> None:
        """Dim rows whose medication number was not circled in the prescription."""
        for row_offset in row_offsets:
            sheet_row = first_row + row_offset
            target_range = f"A{sheet_row}:{rowcol_to_a1(sheet_row, len(FIELDS))}"
            self.worksheet.format(target_range, UNFLAGGED_MEDICATION_FORMAT)


    def _archive_files(self, json_path: Path, image_path: Path, patient_name: str | None) -> None:
        """Move an uploaded JSON and its source image into sent folders"""

        base_name = _sanitize_filename(patient_name or json_path.stem)

        json_sent_dir = json_path.parent / "sent"
        json_sent_dir.mkdir(parents=True, exist_ok=True)

        new_json_path = _unique_path(json_sent_dir, base_name, json_path.suffix)
        json_path.replace(new_json_path)
        logging.info("Renamed JSON to %s", new_json_path.name)

        if not image_path.is_file():
            logging.info("No image found for %s", json_path.name)
            return

        image_sent_dir = image_path.parent / "sent"
        image_sent_dir.mkdir(parents=True, exist_ok=True)
        new_image_path = _unique_path(image_sent_dir, base_name, image_path.suffix)
        image_path.replace(new_image_path)
        logging.info("Renamed image to %s", new_image_path.name)


# JSON conversion
def _parse_prescription_json(path: Path) -> tuple[list[SheetRow], str | None, RowOffsets]:
    """Return worksheet rows, patient name, and unflagged row offsets from an OCR JSON"""

    # Load data in JSON file
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("Skipping invalid JSON file %s", path)
        return [], None, []

    # Validate JSON structure and extract rows
    if not isinstance(payload, dict):
        return [], None, []

    records = payload.get("data_rows")
    if not isinstance(records, list):
        return [], None, []
    if not all(isinstance(record, dict) for record in records):
        return [], None, []

    # Convert records to worksheet rows, ensuring all fields are present and strings.
    rows = [
        [
            "" if record.get(field) is None else str(record.get(field, ""))
            for field in FIELDS
        ]
        for record in records
    ]

    # If nothing is circled, treat every medication as selected.
    has_flagged_medication = any(record.get("is_flagged") is True for record in records)
    if has_flagged_medication:
        unflagged_rows = [
            index
            for index, record in enumerate(records)
            if record.get("is_flagged") is not True
        ]
    else:
        unflagged_rows = []

    # Extract the first non-empty patient name for filename construction.
    patient_name = next(
        (
            str(record["patient_name"]).strip()
            for record in records
            if record.get("patient_name")
        ),
        None,
    )

    return rows, patient_name, unflagged_rows


# File archiving
def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", " ", name)
    return re.sub(r"\s+", " ", cleaned).strip() or "unknown"


def _unique_path(base_dir: Path, stem: str, suffix: str) -> Path:
    candidate = base_dir / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = base_dir / f"{stem} ({index}){suffix}"
        index += 1
    return candidate
