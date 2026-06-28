from __future__ import annotations

import json, logging, os, time
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, Field


# OCR configuration
DEFAULT_MODEL = "gemini-2.5-flash"
SUPPORTED_IMG = {".jpg", ".jpeg"}
OCR_PROMPT    = "OCR this prescription image and accurately " \
                "extract data into the required structured schema."

# API retry policy
ERROR_MAX_RETRIES: dict[int, int] = {
    429: 1, # Rate limit exceeded (resource exhausted)
    503: 3, # Server high demand  (service unavailable)
}

RETRY_DELAY_SEC = 5

### Data Models ###
class PrescriptionRow(BaseModel):
    patient_id:   str = Field(description = "Mã bệnh nhân")
    patient_name: str = Field(description = "Tên bệnh nhân định dạng CamelCase, có dấu tiếng Việt")
    patient_age:  int = Field(description = "Tuổi bệnh nhân dưới dạng số nguyên")
    issue_date:   str = Field(description = "Ngày ở góc phải bên dưới của toa thuốc. Bắt buộc trả về định dạng D-M-YYYY")
    doctor_name:  str = Field(description = "Tên bác sĩ ký ở phía dưới toa thuốc")
    medication:   str = Field(description = "Bắt buộc định dạng thành 'Tên_Biệt_Dược Hàm_Lượng (Hoạt_Chất)', ví dụ " \
                                            "'Thyroberg 100mcg (Levothyroxin)' hoặc 'APO-Erlotinib 150mg (Erlotinib)'")
    quantity:     str = Field(description = "Số lượng thuốc được cấp phát, chỉ ghi số")
    dosage_days:  str = Field(description = "Số ngày sử dụng thuốc, chỉ ghi số")

    #
    is_flagged:  bool = Field(description = "Trả về true nếu số thứ tự của thuốc được khoanh tròn bằng bút trên toa; ngược lại trả về false.")


class PrescriptionTable(BaseModel):
    data_rows: list[PrescriptionRow] = Field(
        description="Danh sách các hàng dữ liệu được bóc tách từ đơn thuốc"
    )


### Custom exceptions for specific error handling ###
class ResourceExhausted(RuntimeError):
    """Raised when quota errors continue after the allowed retry."""


class ServiceUnavailable(RuntimeError):
    """Raised when server high-load errors exhaust their retries."""


# Public OCR workflow
def create_client() -> genai.Client | None:
    """Create a Gemini client from the GEMINI_API_KEY environment variable."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def list_image_files(folder_path: str | Path) -> list[Path]:
    """Return supported image files in deterministic name order"""

    folder = Path(folder_path)
    if not folder.is_dir():
        logging.warning("Folder /images does not exist")
        return []

    return sorted(path
                  for path in folder.iterdir()
                  if path.is_file() and path.suffix.lower() in SUPPORTED_IMG)


class PrescriptionOCR:
    """Process prescription images with one reusable Gemini client"""

    # Constructor. Configuration shared by every image in the batch
    def __init__(self, client: genai.Client, output_dir: str | Path, model: str = DEFAULT_MODEL) -> None:
        self.client     = client
        self.output_dir = Path(output_dir)
        self.model      = model


    # Public entry point for processing one image
    def process_image(self, image_path: Path) -> Path:
        """Extract each prescription image and save its result"""

        result_text = self._extract_prescription(image_path)
        return self._save_result(image_path, result_text)


    # OCR workflow
    def _extract_prescription(self, image_path: Path) -> str:
        """Extract one image, with independent retry counters"""
        image_bytes = image_path.read_bytes()
        retry_counts = {code: 0 for code in ERROR_MAX_RETRIES}

        while True:
            try:
                response = self._generate_ocr_response(image_bytes)
                self._log_token_usage(response, image_path)
                return response.text or ""

            except APIError as error:
                _handle_api_error(error, retry_counts)
                continue

            except Exception as error:
                logging.exception("Unexpected error during prescription extraction.")
                return f"An error occurred: {error}"


    # Internal API calls
    def _generate_ocr_response(self, image_bytes: bytes) -> types.GenerateContentResponse:
        """Send the image and structured response schema to model"""

        return self.client.models.generate_content(
            model    = self.model,
            contents = [OCR_PROMPT, types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")],
            config   = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PrescriptionTable,
                temperature=0.0,
            ),
        )


    @staticmethod
    def _log_token_usage(response: types.GenerateContentResponse, image_path: Path) -> None:
        """Log token counts reported by Gemini for one image."""

        usage = response.usage_metadata
        if usage is None:
            logging.warning("Token usage unavailable for %s", image_path.name)
            return

        logging.info("Token usage for %s: input=%s, output=%s, thoughts=%s, cached=%s, total=%s",
                     image_path.name,
                     usage.prompt_token_count or 0,
                     usage.candidates_token_count or 0,
                     usage.thoughts_token_count or 0,
                     usage.cached_content_token_count or 0,
                     usage.total_token_count or 0,)


    # Output
    def _save_result(self, image_path: Path, result_text: str) -> Path:
        """Save valid JSON as .json, or preserve invalid output as .txt"""

        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            parsed_result = json.loads(result_text)
        except json.JSONDecodeError:
            logging.warning("Model did not return JSON for %s", image_path.name)
            output_file = self.output_dir / f"{image_path.stem}.txt"
            output_file.write_text(result_text, encoding="utf-8")
            return output_file

        output_file = self.output_dir / f"{image_path.stem}.json"
        output_file.write_text(
            json.dumps(parsed_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return output_file


# API error handling
def _handle_api_error(error: APIError, retry_counts: dict[int, int]) -> None:
    """Wait for another attempt, or raise when this error exhausts its limit"""

    error_code = error.code
    message = error.message or str(error)

    if error_code not in ERROR_MAX_RETRIES:
        logging.error("API error %s: %s", error_code, message)
        raise error

    max_retries = ERROR_MAX_RETRIES[error_code]
    retry_counts[error_code] += 1
    retry_number = retry_counts[error_code]

    # If retries reached the limit, raise a specific error
    if retry_number > max_retries:
        if error_code == 429:
            raise ResourceExhausted(message) from error
        elif error_code == 503:
            raise ServiceUnavailable(message) from error

    logging.warning("Err %d; retry in %ds (%d/%d)", error_code, RETRY_DELAY_SEC, retry_number, max_retries)
    time.sleep(RETRY_DELAY_SEC)
