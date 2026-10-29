"""Opt-in live Gemini evaluation for the ten fictional assignment messages."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.models import LeadInput, normalize_analysis
from app.services import GeminiAnalyzer


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 10 fictional messages through the real Gemini API")
    parser.add_argument("--output", default="evidence/gemini-live-evaluation.md")
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.analyzer_mode != "gemini":
        parser.error("Set ANALYZER_MODE=gemini for a live evaluation")

    root = Path(__file__).parent.parent
    messages = json.loads((root / "examples" / "test_messages.json").read_text(encoding="utf-8"))
    analyzer = GeminiAnalyzer(settings)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = ["# Gemini live AI evaluation", "", "**LIVE AI TEST — NOT A FIXTURE TEST.**", "",
             f"Run (UTC): {timestamp}  ", f"Model: `{settings.gemini_model}`", "",
             "| # | Fictional sender | Predicted category | Routed category | Confidence | Ambiguous | Summary / error |",
             "|---:|---|---|---|---:|:---:|---|"]
    failures = 0
    for number, item in enumerate(messages, 1):
        try:
            result = analyzer.analyze(LeadInput(**item))
            routed, summary = normalize_analysis(result)
            detail = summary.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {number} | {item['name']} | {result.category} | {routed} | "
                         f"{result.confidence:.2f} | {'yes' if result.ambiguous else 'no'} | {detail} |")
            print(f"{number:2}. {result.category} -> {routed}; confidence={result.confidence:.2f}; "
                  f"ambiguous={result.ambiguous}; {summary}")
        except Exception as exc:
            failures += 1
            error = f"{type(exc).__name__}: {exc}".replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {number} | {item['name']} | UNVERIFIED | UNVERIFIED | — | — | {error} |")
            print(f"{number:2}. UNVERIFIED; {error}")

    lines.extend(["", f"Result: {10 - failures}/10 verified by a live API response; {failures}/10 unverified.", ""])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Live report written to {output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
