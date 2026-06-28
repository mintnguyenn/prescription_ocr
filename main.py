from __future__ import annotations

import logging, sys, time
from pathlib import Path

from google.genai.errors import APIError

from src.prescription_ocr import (PrescriptionOCR, ResourceExhausted, ServiceUnavailable,
                                  create_client, list_image_files)

from src.sheet_uploader import PrescriptionSheetUploader, get_gspread_client, open_worksheet

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

IMAGES_DIR = Path("images")
OUTPUT_DIR = Path("outputs")
SHEET_NAME = "Sheet1"

GSHEET_CREDS_PATH = "credentials.json" # Replace with your own Google service-account credential.json path
DEFAULT_SHEET_ID  = "1FdEEqFUeXIywNTogPngjQ9b71Wv8AX_TMzN-GUWtdfw" # Replace with your own Google Sheet ID


def main() -> int:
    ocr_client = create_client()
    if ocr_client is None:
        logging.error("API key not configured. Set the GEMINI_API_KEY environment variable")
        return 1

    start_time = time.perf_counter()
    try:
        ocr = PrescriptionOCR(ocr_client, OUTPUT_DIR)
        sheet_client = get_gspread_client(GSHEET_CREDS_PATH)
        worksheet = open_worksheet(sheet_client, DEFAULT_SHEET_ID, SHEET_NAME)
        sheet_uploader = PrescriptionSheetUploader(worksheet)

        images = list_image_files(IMAGES_DIR)
        if not images:
            logging.info("No images found in %s", IMAGES_DIR)
            return 0

        sheet_uploader.upload_batch_marker()

        total_images = len(images)
        for image_number, image_path in enumerate(images, start=1):
            print(file=sys.stderr, flush=True)
            logging.info("OCR IMG %d/%d: %s", image_number, total_images, image_path.name)

            try:
                result_path = ocr.process_image(image_path)

            except ResourceExhausted:
                logging.error("Rate limit still exceeded after retries; stopping batch")
                return 1

            except ServiceUnavailable:
                logging.error("Server still unavailable after retries; skipping %s", image_path.name)

            except APIError:
                return 1

            else:
                if result_path.suffix.lower() == ".json":
                    # Only upload if OCR succeeded and produced a JSON result
                    sheet_uploader.upload_prescription(image_path, result_path)
                else:
                    logging.warning("OCR failed for %s; kept %s for inspection", image_path.name, result_path.name)

        return 0

    finally:
        elapsed_seconds = time.perf_counter() - start_time
        logging.info("Completed after %.1f seconds", elapsed_seconds)

        try:
            ocr_client.close()
        except Exception:
            logging.exception("Failed to close genai client")


if __name__ == "__main__":
    raise SystemExit(main())
