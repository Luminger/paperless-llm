"""Dump the OpenAPI schema (the API contract) to a JSON file.

Usage: python -m app.openapi_export [outfile]
The frontend generates its TS types from this via openapi-typescript
(`npm run gen:api`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    # The app builds without real deployment config.
    os.environ.setdefault("PAPERLESS_LLM_CONFIG", "/nonexistent")
    os.environ.setdefault("PLLM_PAPERLESS__BASE_URL", "http://paperless.invalid")
    os.environ.setdefault("PLLM_PAPERLESS__TOKEN", "schema-export")
    os.environ.setdefault("PLLM_LLM__AGENT__BASE_URL", "http://llm.invalid/v1")
    os.environ.setdefault("PLLM_LLM__AGENT__MODEL", "schema-export")

    from app.main import create_app

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    schema = create_app().openapi()
    with out.open("w") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
