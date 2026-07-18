from app.paperless.client import PaperlessClient, PaperlessError, make_client
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
    "make_client",
    "StoragePath",
    "Tag",
]
