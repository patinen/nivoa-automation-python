# Nivoa end-to-end live test

**LIVE E2E TEST — REAL GEMINI, GOOGLE SHEETS AND SMTP.**

Status: **EXECUTED — PASSED**
Run ID: `nivoa-final-01`
Started (UTC): 2026-10-29T18:04:41+00:00
Reported (UTC): 2026-10-29T18:07:48+00:00

- Webhook persistence: **10/10 verified** (`created=true`: 10/10).
- Completed SQLite messages: **10/10**.
- Expected categories matched: **10/10**.
- Unique Google Sheets rows: **10/10**.
- SMTP acceptances: **6/6**; quotation requests: 3 expected; human review: 3 expected.
- Recipient restriction: **passed**.
- Idempotent replay: **passed**.

| # | Fictional sender | Expected | Analysis | DB status | sheet_done / rows | email_done / SMTP accepted |
|---:|---|---|---|---|---|---|
| 1 | Matti Esimerkki | tarjouspyyntö | tarjouspyyntö | completed | 1 / 1 | 1 / 1 |
| 2 | Liisa Testinen | asiakkaan kysymys | asiakkaan kysymys | completed | 1 / 1 | 0 / 0 |
| 3 | SEO Masters | roskaposti | roskaposti | completed | 1 / 1 | 0 / 0 |
| 4 | Pekka Kokeilu | asiakkaan kysymys | asiakkaan kysymys | completed | 1 / 1 | 0 / 0 |
| 5 | Anna Malli | tarjouspyyntö | tarjouspyyntö | completed | 1 / 1 | 1 / 1 |
| 6 | Jukka Esimerkki | ihmiselle | ihmiselle | completed | 1 / 1 | 1 / 1 |
| 7 | Sari Testi | ihmiselle | ihmiselle | completed | 1 / 1 | 1 / 1 |
| 8 | Tomi Kokeilu | tarjouspyyntö | tarjouspyyntö | completed | 1 / 1 | 1 / 1 |
| 9 | Hanna Malli | muu | muu | completed | 1 / 1 | 0 / 0 |
| 10 | Kalle Esimerkki | ihmiselle | ihmiselle | completed | 1 / 1 | 1 / 1 |

SMTP acceptance confirms that the configured SMTP server accepted a message; it does not prove final Inbox delivery.
