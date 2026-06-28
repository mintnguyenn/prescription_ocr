# Prescription Reader

Batch OCR tool for reading Vietnamese prescription images, extracting structured medication data with an LLM model (currently Google Gemini), then writing the result to Google Sheets.

The project is intentionally simple to run:

```bash
python main.py
```

Put prescription images in `images/`, run the script, and successfully processed files will be moved into `sent/` folders.

## What it does

For each image in `images/`, the script:

1. Sends the prescription image to the LLM model.
2. Extracts patient and medication data into structured JSON.
3. Saves the OCR result into `outputs/`.
4. Writes prescription rows to Google Sheets.
5. Moves uploaded JSON files to `outputs/sent/`.
6. Moves uploaded source images to `images/sent/`.

Each run also writes a dated batch marker row to the sheet, so it is easy to see which prescriptions were processed together.

Medication rows that are not circled/flagged in the prescription are dimmed in Google Sheets. If no medication is circled, the script treats all medications as selected.

## Project structure

```text
prescription-reader/
|-- main.py
|-- requirements.txt
|-- credentials.json          # local only, ignored by git
|-- images/
|   `-- .gitkeep
|-- outputs/
|   `-- .gitkeep
`-- src/
    |-- prescription_ocr.py
    `-- sheet_uploader.py
```

## Requirements

- Python 3.12+
- Gemini API key
- Google Cloud service-account credentials for Google Sheets
- A Google Sheet shared with the service-account email

Install dependencies:

```bash
pip install -r requirements.txt
```

## Setup

### 1. Gemini API key

Set the `GEMINI_API_KEY` environment variable.

PowerShell:

```powershell
$env:GEMINI_API_KEY="your-gemini-api-key"
```

For a persistent setup, add it to your system/user environment variables instead of setting it only in the current terminal.

### 2. Google Sheets credentials

Create a Google service-account credential file and save it as:

```text
credentials.json
```

Then share your Google Sheet with the service account email found inside that credential file.

`credentials.json` is ignored by git and should never be committed.

### 3. Configure the target Sheet

In `main.py`, update:

```python
DEFAULT_SHEET_ID = "your-google-sheet-id"
SHEET_NAME = "Sheet1"
```

The Sheet ID is the long ID in a Google Sheets URL:

```text
https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit
```

## How to run

Put `.jpg` or `.jpeg` prescription images into:

```text
images/
```

Then run:

```bash
python main.py
```

Example log:

```text
INFO: OCR IMG 1/3: patient-a.jpg
INFO: Token usage for patient-a.jpg: input=273, output=731, thoughts=2949, cached=0, total=3953
INFO: Wrote 6 rows from patient-a.json to Sheet1!A120:I125
INFO: Renamed JSON to Nguyen Van A.json
INFO: Renamed image to Nguyen Van A.jpg
INFO: Completed after 42.7 seconds
```

## Output behavior

Successful OCR creates JSON files in:

```text
outputs/
```

After the JSON is successfully written to Google Sheets, both the JSON and source image are archived:

```text
outputs/sent/
images/sent/
```

If OCR returns invalid JSON, the raw result is saved as `.txt` in `outputs/` for manual inspection.

## Google Sheet columns

Rows are written from column A using this order:

| A          | B            | C           | D          | E          | F           | G          | H        | I           |
| ---------- | ------------ | ----------- | ---------- | ---------- | ----------- | ---------- | -------- | ----------- |
| patient_id | patient_name | patient_age | issue_date | department | doctor_name | medication | quantity | dosage_days |

The OCR JSON may also include `is_flagged`, but this field is used only for sheet formatting and is not written as a column.

## Error handling

The OCR module currently handles the two common Gemini API errors:

- `429`: quota/rate limit. The script retries once, then stops the batch.
- `503`: service unavailable/high demand. The script retries three times, then skips that image.

Other API errors are logged and stop the run.

## Git hygiene

The repository ignores local secrets and patient data:

- `credentials.json`
- `.env`
- `images/*`
- `outputs/*`
- virtual environments and Python cache files

Keep real prescription images, OCR results, and credentials out of git.
