from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Document(BaseModel):
    doc_id: str = Field(min_length=1)
    doc_type: str = Field(min_length=1)  # derived from directory (e.g. "processes")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    source: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def now(
        cls,
        *,
        doc_id: str,
        doc_type: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        source: str = "seed",
    ) -> "Document":
        now = datetime.now(timezone.utc)
        return cls(
            doc_id=doc_id,
            doc_type=doc_type,
            title=title,
            content=content,
            tags=tags or [],
            source=source,
            created_at=now,
            updated_at=now,
        )


class SearchHit(BaseModel):
    score: float
    document: Document

