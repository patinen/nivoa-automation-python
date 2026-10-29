# Nivoa – solution summary

## Implementation

The application processes accounting-firm contact forms with Python, FastAPI, SQLite, and a separate worker. The webhook validates and durably stores the original message before returning HTTP 202. The worker uses Gemini structured output to classify the message, routes uncertainty to `ihmiselle`, writes a concise Finnish summary to Google Sheets, and sends owner email only for quotation requests and human-review cases.

Analysis, Sheets, and email stages are checkpointed independently. Transient integration failures receive bounded persistent retry; permanent failures remain visible as `needs_attention`. Google Sheets uses the internal message ID for reconciliation and duplicate reduction. SMTP acceptance is recorded, although SMTP cannot provide a strict exactly-once guarantee after ambiguous connection failures.

Python and FastAPI were selected because the integration flow remains compact, typed, and straightforward to test without a larger framework. SQLite is appropriate for the assignment's low volume and makes durable state and recovery events directly inspectable.

## Verified results

- Complete offline suite: **67 passed** with one TestClient deprecation warning.
- Credential-free offline E2E: **10/10 completed**, **10 mock Sheet rows**, **6 mock owner notifications**, and a replay with **zero new side effects**.
- Live Gemini evaluation: **10/10 responses verified** for the supplied fictional messages.
- Live Google Sheets test: **10 unique message IDs**, including repeated append attempts without duplicate rows.
- Live SMTP test: server acceptance verified for fictional quotation and human-review notifications sent only to the configured owner/test recipient.
- Complete opt-in live E2E: **10/10 persisted and completed**, **10/10 category matches**, **10 unique Sheets rows**, **6/6 SMTP acceptances**, and an idempotent replay with no additional rows or SMTP acceptances.

SMTP acceptance confirms server acceptance, not final Inbox delivery. The included evidence contains sanitized observations and no credentials or private recipient address.

## Production considerations

For a real deployment I would add a separate alert channel for SMTP outages, formal personal-data retention and deletion rules, managed secrets, access controls, monitoring, tested backups, and an appropriate privacy assessment. Multiple concurrent workers would also require stronger coordination to improve Sheets delivery guarantees.
