# Nivoa lead automation

A durable automation for a Finnish accounting firm. It accepts website enquiries, stores the original message before acknowledging it, classifies the enquiry, records it in Google Sheets, and notifies the owner when a quotation request or human review is needed.

```text
FastAPI webhook → SQLite durable inbox → Gemini → Google Sheets → SMTP
```

The worker checkpoints each external stage in SQLite. A failed integration therefore leaves the original message and completed stages available for controlled retry.

Python keeps the integration code and failure handling explicit and easy to test. FastAPI provides typed request validation, a small authenticated HTTP surface, and automatic HTTP error responses without introducing a larger web framework. SQLite fits the assignment's low message volume while providing durable transactions and a transparent event history.

## Quick start: fully offline

Requires Python 3.11 or newer. From a clean checkout in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m app.offline_demo
```

`app.offline_demo` needs no `.env`, API key, Google account, or SMTP credentials. It creates an isolated temporary SQLite database, submits all ten fictional assignment messages through the real FastAPI webhook, runs the real worker logic with fixture/mock adapters, and asserts:

- 10 persisted and completed messages with the expected fixture classifications;
- exactly 10 unique mock Sheets rows;
- exactly 6 mock owner notifications (3 quotation requests and 3 human-review cases); and
- an HTTP replay with the same idempotency keys creates no records or side effects.

It exits nonzero on any mismatch. Retain local artifacts for inspection only when needed:

```powershell
python -m app.offline_demo --work-dir .\offline-demo-output
```

The target directory must be empty. The offline run verifies pipeline behavior—not Gemini quality, Google Sheets connectivity, SMTP acceptance, or inbox delivery.

Run the automated tests with:

```powershell
python -m pytest -q
```

## Configuration

Copy the safe template for normal API/worker use:

```powershell
Copy-Item .env.example .env
```

All entry points load the project-root `.env` automatically. Process environment variables take precedence, and relative database/credential paths resolve from the project root. Restart long-running API and worker processes after changing configuration.

Inspect effective non-secret settings:

```powershell
python -m app.config_diagnose
```

### Offline API and worker

The `.env.example` defaults to `ANALYZER_MODE=fixture`, `SHEET_MODE=mock`, and `EMAIL_MODE=mock`:

```powershell
# Terminal 1
uvicorn app.api:app --host 127.0.0.1 --port 8000

# Terminal 2
python -m app.worker

# Terminal 3
python -m app.demo
python -m app.inspect
```

The default demo uses deterministic content-derived idempotency keys. Repeating it returns `created: false` and does not reprocess completed messages. A deliberately new batch requires an explicit option:

```powershell
python -m app.demo --new-run --run-id intentional-batch-01
```

Two genuine submissions remain distinct when callers provide different keys. Content alone is never used as global production deduplication.

## Real integrations

Set these values in `.env` or the process environment. Never commit `.env` or files under `.secrets/`.

### Gemini

```dotenv
ANALYZER_MODE=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_TIMEOUT_SECONDS=30
```

Gemini uses structured JSON validated by the Pydantic `Analysis` model. Uncertain or mixed-intent results are routed to `ihmiselle`; summaries are reduced to one concise Finnish sentence.

### Google Sheets OAuth

Create a Google Cloud Desktop OAuth client, enable Google Sheets API, and place the downloaded client configuration at `.secrets/google-oauth-client.json`. Configure:

```dotenv
SHEET_MODE=google
GOOGLE_AUTH_MODE=oauth
GOOGLE_SHEET_ID=...
GOOGLE_SHEET_TAB=Leads
GOOGLE_OAUTH_CLIENT_FILE=.secrets/google-oauth-client.json
GOOGLE_TOKEN_FILE=.secrets/google-token.json
```

Perform interactive authorization once, outside the API and worker:

```powershell
python -m app.google_auth
python -m app.sheets_diagnose
```

The worker never opens a browser. It refreshes and persists an expired access token when a valid refresh token exists. External OAuth apps left in Google's **Testing** publishing status commonly receive refresh tokens limited to seven days; reauthorize or move the consent screen to an appropriate production status when required. Existing service-account support remains available with `GOOGLE_AUTH_MODE=service_account` and `GOOGLE_CREDENTIALS_FILE`.

The worksheet columns are `Aika`, `Nimi`, `Yhteystieto`, `Luokka`, `Tiivistelmä`, and `Message ID`. Values use RAW input. An empty worksheet receives a header, and the message ID is checked before appending.

### Gmail SMTP

Use a Gmail App Password rather than the normal account password:

```dotenv
EMAIL_MODE=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_TIMEOUT_SECONDS=30
SMTP_USERNAME=owner@example.com
SMTP_PASSWORD=...
EMAIL_FROM=owner@example.com
OWNER_EMAIL=owner@example.com
```

Port 465 with `SMTP_SECURITY=ssl` is also supported. Lead notifications are sent only for `tarjouspyyntö` and `ihmiselle`, always to `OWNER_EMAIL`, never to fictional contacts.

## Opt-in live end-to-end test

Use a dedicated database and test worksheet. Start the API with real modes in terminal 1:

```powershell
$env:APP_ENV = "development"
$env:WEBHOOK_TOKEN = "local-live-test-token"
$env:DATABASE_PATH = "data/live-e2e.sqlite3"
$env:ANALYZER_MODE = "gemini"
$env:GEMINI_API_KEY = Read-Host "Gemini API key" -MaskInput
$env:SHEET_MODE = "google"
$env:GOOGLE_AUTH_MODE = "oauth"
$env:GOOGLE_SHEET_ID = "YOUR_TEST_SPREADSHEET_ID"
$env:GOOGLE_SHEET_TAB = "Leads"
$env:GOOGLE_TOKEN_FILE = (Resolve-Path ".\.secrets\google-token.json").Path
$env:EMAIL_MODE = "smtp"
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_SECURITY = "starttls"
$env:SMTP_USERNAME = "YOUR_GMAIL_ADDRESS"
$env:SMTP_PASSWORD = Read-Host "Gmail App Password" -MaskInput
$env:EMAIL_FROM = $env:SMTP_USERNAME
$env:OWNER_EMAIL = $env:SMTP_USERNAME
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

In terminal 2, configure the same values and run a named batch:

```powershell
$env:DEMO_URL = "http://127.0.0.1:8000/webhook"
$runId = "recruiter-live-01"
python -m app.e2e_live --confirm-live --run-id $runId --timeout-seconds 300
python -m app.inspect
```

The second terminal must have the same integration variables as terminal 1. The runner performs a local configuration preflight before submission, then uses the actual HTTP webhook and worker logic. `--confirm-live` is mandatory because the command makes real API calls, appends rows, and sends owner email. It never deletes existing database or spreadsheet data.

Replay the same batch safely with the same database and run ID:

```powershell
python -m app.e2e_live --confirm-live --run-id $runId --timeout-seconds 300
```

Omit `--run-id` only when deliberately requesting a new uniquely identified batch. The report at `evidence/e2e-live-test.md` records observed states, classifications, Sheets row counts, SMTP acceptance events, recipients, replay results, and partial failures. SMTP acceptance does not prove inbox delivery.

Individual opt-in checks are also available:

```powershell
python -m app.live_eval
python -m app.live_sheets --confirm-write
python -m app.live_smtp --confirm-send
```

## Inspection and recovery

```powershell
python -m app.inspect
python -m app.inspect --recoverable
python -m app.inspect --id <MESSAGE_ID>
python -m app.audit_duplicates
python -m app.worker --retry-id <MESSAGE_ID>
python -m app.worker --once
```

Do not resubmit a failed webhook merely to retry an external stage. Requeue its existing message ID. Analysis, Sheets, and email checkpoints prevent already completed stages from running again.

The optional demo-cleanup command is destructive only with an explicit apply flag. Always review first:

```powershell
python -m app.cleanup_demo --dry-run
python -m app.cleanup_demo --apply
```

`--apply` creates a timestamped SQLite backup before removing only exact fictional duplicates and their dependent events.

## Reliability and limitations

- SQLite commits the original message before returning HTTP 202. WAL and `synchronous=FULL` are enabled.
- Transient timeouts, HTTP 408/429, and 5xx failures use bounded, jittered job-level retry. Retry scheduling and stage failures are persisted as events.
- Permanent configuration/authentication failures become `needs_attention`; completed checkpoints and original content remain intact.
- Owner failure alerts are emitted only at terminal failure. A normalized incident fingerprint suppresses equivalent alerts for 24 hours while retaining every failure event in SQLite.
- Sheets reconciles an ambiguous append by checking the message ID. This reduces duplicates but is not strict exactly-once under concurrent workers; use one worker for this demonstration.
- SMTP cannot provide exactly-once delivery when a connection fails after server acceptance but before acknowledgement, or if the process stops between SMTP acceptance and its SQLite checkpoint. Stable email `Message-ID` and internal IDs make possible duplicates identifiable.
- SQLite is appropriate for this assignment's low volume. Multi-worker production scaling would require stronger coordination.

## Repository safety

The supplied `.gitignore` excludes `.env`, `.secrets/`, credential/token files, SQLite databases and WAL/SHM files, backups, mock/live data, virtual environments, caches, coverage, and build output. `.env.example` contains placeholders only. Before publishing, review staged files and history in a real Git checkout; never paste credential values into issue reports or evidence files.

Docker support is provided for a single API and worker sharing `./data`. Do not scale the worker service without revisiting locking and Sheets concurrency.
