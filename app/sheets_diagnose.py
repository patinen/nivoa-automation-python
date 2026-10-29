"""Read-only Google Sheets connectivity and configuration diagnosis."""
import argparse

from app.config import Settings
from app.google_diagnostics import diagnose_google_exception
from app.services import GoogleSheet


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Google Sheets access without writing data")
    parser.parse_args()
    settings = Settings.from_env()
    if settings.sheet_mode != "google":
        parser.error("Set SHEET_MODE=google before running this diagnosis")

    try:
        sheet = GoogleSheet(settings)
        worksheet = sheet.worksheet_metadata()
        rows = sheet.rows()
        print("Google Sheets read-only diagnosis passed.")
        print("Authentication: valid")
        print("Spreadsheet metadata: accessible")
        print(f"Worksheet: found ({worksheet.get('title', settings.sheet_tab)})")
        print(f"Readable rows: {len(rows)}")
        return 0
    except Exception as exc:
        diagnostic = diagnose_google_exception(exc, "read-only diagnosis")
        print("Google Sheets read-only diagnosis failed.")
        print(f"Operation: {diagnostic.operation}")
        print(f"HTTP status: {diagnostic.status_code if diagnostic.status_code is not None else 'not available'}")
        print(f"Reason: {diagnostic.reason}")
        print(f"Message: {diagnostic.message}")
        print(f"Suggested action: {diagnostic.suggestion}")
        print("Sensitive credentials, authorization headers, and request URLs are not shown.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
