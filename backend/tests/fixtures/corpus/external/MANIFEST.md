# Corpus — provenance & licenses

Real, publicly shared sample PDFs — the entire seed corpus (nothing is
generated). All files are redistributable; none contain personal data of
the project authors. Checksums in `SHA256SUMS`.

| File | What | Language | Source | License |
|---|---|---|---|---|
| `de-invoice-kraxi.pdf` | The classic ZUGFeRD sample invoice (Kraxi GmbH paper planes, Rechnung 2014-03, 1.005,55 EUR) | DE | [ZUGFeRD/mustangproject](https://github.com/ZUGFeRD/mustangproject) `library/src/test/resources/zugferd_invoice.pdf` | Apache-2.0 |
| `de-invoice-zugferd-mustang.pdf` | Born-digital invoice, Bei Spiel GmbH, RE-20170509/505, 571,04 EUR | DE | mustangproject `MustangGnuaccountingBeispielRE-20170509_505.pdf` | Apache-2.0 |
| `de-invoice-weclapp-re1001.pdf` | ERP-generated invoice RE1001, 963,12 EUR | DE | mustangproject `MustangBeispiel20221026.pdf` | Apache-2.0 |
| `de-invoice-zugferd-einfach.pdf` | EN16931 e-invoice sample, rendered XML visualization | DE | mustangproject `EN16931_Einfach.pdf` | Apache-2.0 |
| `de-invoice-zugferd-teilrechnung.pdf` | EN16931 partial-invoice sample, 3 pages | DE | mustangproject `EN16931_1_Teilrechnung.pdf` | Apache-2.0 |
| `de-invoice-datev-belegverfilmung.pdf` | DATEV document-microfilming sample invoice | DE | mustangproject `ZTESTZUGFERD_1_INVDSS_012015738820PDF-1.pdf` | Apache-2.0 |
| `de-letter-bmf-pauschbetraege-2024.pdf` | Official letter (BMF-Schreiben) with rate table, 2024-02-12 | DE | [bundesfinanzministerium.de](https://www.bundesfinanzministerium.de/Content/DE/Downloads/BMF_Schreiben/Weitere_Steuerthemen/Betriebspruefung/Richtsatzsammlung/2024-02-12-pauschbetraege-2024.pdf) | Public domain (amtliches Werk, § 5 UrhG) |
| `en-letter-frb-sr2404.pdf` | Federal Reserve supervisory letter SR 24-4, 8 pages | EN | [federalreserve.gov](https://www.federalreserve.gov/supervisionreg/srletters/sr2404.pdf) | Public domain (US Government work) |
| `en-letter-cia-duncan.pdf` | Declassified letter to Charles W. Duncan Jr., 8-page scan, no text layer | EN | [archive.org / CIA Reading Room](https://archive.org/details/cia-readingroom-document-0000700721) | Public domain (declassified US Government record) |
| `en-form-irs-f1040.pdf` | IRS Form 1040 — dense form/table layout | EN | [irs.gov](https://www.irs.gov/pub/irs-pdf/f1040.pdf) | Public domain (US Government work) |
| `en-invoice-scan-1956.pdf` | Declassified typewritten invoice scan (Perkin-Elmer), no text layer | EN | [archive.org / CIA Reading Room](https://archive.org/details/cia-readingroom-document-cia-rdp89b00709r000300680042-6) | Public domain (declassified US Government record) |
| `en-invoice-scan-1958.pdf` | Declassified typewritten invoice scan ("Invoice No. 4-8"), no text layer | EN | [archive.org / CIA Reading Room](https://archive.org/details/cia-readingroom-document-cia-rdp89b00709r000300640003-3) | Public domain (declassified US Government record) |
| `en-invoice-ivy-1971.pdf` | Ivy Network invoice to campus media (WXPN), 1971 typewritten scan | EN | [archive.org](https://archive.org/details/IvyNetworkInvoicetoCampusMediaforWXPNMarch1971) | Publicly shared archival record (no known restrictions) |

The `*-scan-*`, `en-letter-cia-duncan`, and `en-invoice-ivy-1971` files
carry **no text layer** — the ad-hoc paperless instance produces its own
tesseract OCR for them, the authentic input for `ocr_document`
similarity comparisons.

The messy metadata (junk titles like `scan_0001`, near-duplicate
correspondents `Kraxi`/`Kraxi GmbH`, duplicate tags `Rechnung`/`invoice`,
bad casing, orphans) is applied by the seeder (`app/seeding.py`) — the
documents themselves are untouched originals.
