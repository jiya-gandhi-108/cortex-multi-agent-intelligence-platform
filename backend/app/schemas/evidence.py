"""Evidence objects carried through the workflow state."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field


def citation_key_for(normalized_url: str, length: int = 6) -> str:
    """Deterministic, collision-resistant short id the LLM cites, e.g. ``S3f9a2c``.

    Derived from the normalised URL, so parallel branches assign the same key to
    the same page without coordination, and duplicates merge naturally.
    """
    return "S" + hashlib.sha256(normalized_url.encode()).hexdigest()[:length]


class SourceRecord(BaseModel):
    citation_key: str
    url: str
    normalized_url: str
    title: str | None = None
    domain: str
    publication_date: datetime | None = None
    retrieved_at: datetime
    content: str = Field(description="Cleaned excerpt used as evidence (length-capped)")
    fetched: bool = Field(description="True if the page itself was fetched; False = snippet only")
    task_key: str
    search_provider: str
    query: str
