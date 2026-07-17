from app.paperless.client import PaperlessClient, PaperlessError
from app.paperless.schemas import (
    Correspondent,
    CustomField,
    Document,
    DocumentType,
    Page,
    StoragePath,
    Tag,
)

__all__ = [
    "Correspondent",
    "CustomField",
    "Document",
    "DocumentType",
    "Page",
    "PaperlessClient",
    "PaperlessError",
    "StoragePath",
    "Tag",
]
