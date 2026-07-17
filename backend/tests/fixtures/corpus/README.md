# Seed corpus

Real, publicly shared PDFs only — see `external/MANIFEST.md` for
provenance, licenses, and checksums. Nothing is generated.

`app/seeding.py` uploads them (`paperless-llm seed`) and layers the
deliberately messy taxonomy on top: near-duplicate correspondents,
duplicate tags/types, junk titles, orphans, and a paperless Inbox tag
on the designated fresh arrivals.

No personal data anywhere in this corpus.
