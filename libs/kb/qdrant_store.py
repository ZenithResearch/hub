from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PointStruct,
    VectorParams,
)

from libs.common.ids import stable_uuid_for_kb_doc

from .interfaces import EmbeddingProvider, VectorStore
from .models import Document, SearchHit


class QdrantVectorStore(VectorStore):
    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        collection: str,
        embedding_provider: EmbeddingProvider,
        vector_dim: int,
    ) -> None:
        self._client = QdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._emb = embedding_provider
        self._vector_dim = vector_dim

        if self._emb.vector_dim != self._vector_dim:
            raise ValueError("Embedding provider dim does not match vector_dim")

    def upsert_documents(self, docs: list[Document]) -> None:
        if not docs:
            return
        self._ensure_collection()

        vectors = self._emb.embed([self._doc_text(d) for d in docs])
        points: list[PointStruct] = []

        for doc, vec in zip(docs, vectors, strict=True):
            payload = _doc_to_payload(doc)
            points.append(
                PointStruct(
                    id=stable_uuid_for_kb_doc(doc.doc_id),
                    vector=vec,
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=self._collection, points=points)

    def search(
        self, query: str, doc_types: Optional[list[str]] = None, k: int = 5
    ) -> list[SearchHit]:
        self._ensure_collection()
        vec = self._emb.embed([query])[0]

        flt: Optional[Filter] = None
        if doc_types:
            flt = Filter(
                must=[
                    FieldCondition(
                        key="doc_type",
                        match=MatchAny(any=list(doc_types)),
                    )
                ]
            )

        # qdrant-client v1.17+ uses `query_points`; older versions use `search`.
        if hasattr(self._client, "search"):
            results = self._client.search(  # type: ignore[attr-defined]
                collection_name=self._collection,
                query_vector=vec,
                query_filter=flt,
                limit=k,
                with_payload=True,
            )
            points = results
        else:
            resp = self._client.query_points(
                collection_name=self._collection,
                query=vec,
                query_filter=flt,
                limit=k,
                with_payload=True,
            )
            points = getattr(resp, "points", [])

        hits: list[SearchHit] = []
        for r in points:
            payload = dict(r.payload or {})
            hits.append(
                SearchHit(
                    score=float(r.score),
                    document=_payload_to_doc(payload),
                )
            )
        return hits

    def _ensure_collection(self) -> None:
        try:
            exists = self._client.collection_exists(collection_name=self._collection)
        except UnexpectedResponse:
            exists = False

        if exists:
            return

        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=self._vector_dim, distance=Distance.COSINE),
        )

    @staticmethod
    def _doc_text(doc: Document) -> str:
        # Simple representation used for embedding.
        return f"{doc.title}\n\n{doc.content}"


def _doc_to_payload(doc: Document) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "title": doc.title,
        "content": doc.content,
        "tags": list(doc.tags),
        "source": doc.source,
        "created_at": doc.created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": doc.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _payload_to_doc(payload: dict[str, Any]) -> Document:
    return Document(
        doc_id=str(payload.get("doc_id", "")),
        doc_type=str(payload.get("doc_type", "knowledge")),
        title=str(payload.get("title", "")),
        content=str(payload.get("content", "")),
        tags=list(payload.get("tags", [])) if payload.get("tags") is not None else [],
        source=str(payload.get("source", "")),
        created_at=_parse_dt(payload.get("created_at")),
        updated_at=_parse_dt(payload.get("updated_at")),
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v).astimezone(timezone.utc)
    return datetime.now(timezone.utc)

