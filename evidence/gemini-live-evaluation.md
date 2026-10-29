# Gemini live AI evaluation

**LIVE AI TEST — NOT A FIXTURE TEST.**

Run (UTC): 2026-10-29T13:53:11+00:00
Model: `gemini-3.5-flash-lite`

| # | Fictional sender | Predicted category | Routed category | Confidence | Ambiguous | Summary / error |
|---:|---|---|---|---:|:---:|---|
| 1 | Matti Esimerkki | tarjouspyyntö | tarjouspyyntö | 0.95 | no | Rakennusyritys tiedustelee kirjanpitopalvelun kuukausihintaa. |
| 2 | Liisa Testinen | asiakkaan kysymys | asiakkaan kysymys | 0.95 | no | Asiakas kysyy neljännesvuosittaisen arvonlisäveroilmoituksen jättöajankohtaa. |
| 3 | SEO Masters | roskaposti | roskaposti | 0.99 | no | Viestissä tarjotaan hakukoneoptimointipalvelua verkkosivustolle. |
| 4 | Pekka Kokeilu | asiakkaan kysymys | asiakkaan kysymys | 0.99 | no | Asiakas pyytää tarkistamaan laskun numero 1043 väärän summan. |
| 5 | Anna Malli | tarjouspyyntö | tarjouspyyntö | 0.95 | no | Uusi yrittäjä tiedustelee kirjanpito- ja palkkahallintopalveluita perustettavalle toiminimelleen. |
| 6 | Jukka Esimerkki | ihmiselle | ihmiselle | 1.00 | yes | Lähettäjä ei kirjoittanut viestiä. |
| 7 | Sari Testi | ihmiselle | ihmiselle | 0.40 | yes | Asiakas pyytää soittamaan. |
| 8 | Tomi Kokeilu | tarjouspyyntö | tarjouspyyntö | 0.95 | no | Potentiaalinen asiakas tiedustelee osakeyhtiön tilinpäätöksen hintaa. |
| 9 | Hanna Malli | muu | muu | 0.95 | no | Asiakas kiittää eilisestä tapaamisesta ja lupaa lähettää puuttuvat kuitit huomenna. |
| 10 | Kalle Esimerkki | ihmiselle | ihmiselle | 0.50 | yes | Asiakas haluaa tarjouksen, mutta on epävarma kirjanpitäjän tarpeesta ja pyytää suositusta. |

Result: 10/10 verified by a live API response; 0/10 unverified.
