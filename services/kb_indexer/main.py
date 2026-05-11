"""KB indexer — walks base/ and spaces/ trees, indexes non-MOC markdown into Qdrant.

Document type is derived from the immediate parent directory name (e.g. a file at
base/processes/foo.md gets doc_type="processes"). MOC files (index.md) are skipped
as content — they define structure, not searchable knowledge.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from libs.common.config import IndexerSettings
from libs.common.logging import configure_logging, get_logger
from libs.kb.embeddings import DeterministicEmbeddingProvider
from libs.kb.models import Document
from libs.kb.qdrant_store import QdrantVectorStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_documents_from_tree(root: Path) -> list[Document]:
    """Walk a content tree (base/ or spaces/) and index all non-MOC markdown files.

    Rules:
    - index.md files are MOCs — skipped as content documents
    - doc_type is the immediate parent directory name relative to root
    - Files directly under root (not in a subdirectory) get doc_type = root.name
    - Nested directories deeper than one level are walked; doc_type stays the
      first-level subdirectory name (e.g. base/processes/subdir/foo.md → "processes")
    """
    docs: list[Document] = []

    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "index.md":
            continue  # MOC file — structural, not searchable content

        # Determine doc_type from the first directory level under root
        rel = path.relative_to(root)
        parts = rel.parts
        doc_type = parts[0] if len(parts) > 1 else root.name

        raw = _parse_frontmatter_markdown(path.read_text(encoding="utf-8"))
        raw.setdefault("source", str(path))
        raw.setdefault("doc_type", doc_type)

        # Use filename stem as doc_id if not declared in frontmatter
        raw.setdefault("doc_id", f"{doc_type}.{path.stem}")

        # Title defaults to the filename stem (spaces restored from underscores/hyphens)
        raw.setdefault("title", path.stem.replace("-", " ").replace("_", " "))

        if not raw.get("content", "").strip():
            continue  # skip empty files

        docs.append(_doc_from_mapping(raw, source=str(path)))

    return docs


def _doc_from_mapping(raw: dict[str, Any], *, source: str) -> Document:
    raw = dict(raw)
    raw.setdefault("source", source)
    raw.setdefault("created_at", _now_iso())
    raw.setdefault("updated_at", raw["created_at"])
    return Document.model_validate(raw)


def _parse_frontmatter_markdown(text: str) -> dict[str, Any]:
    """Parse optional YAML frontmatter + markdown body."""
    text = text.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return {"content": text.strip()}

    end = text.find("\n---\n", 4)
    if end == -1:
        return {"content": text.strip()}

    frontmatter = text[4:end]
    body = text[end + 5 :].strip()

    try:
        import yaml  # type: ignore
    except Exception:
        return {"content": body}

    meta = yaml.safe_load(frontmatter) or {}
    if not isinstance(meta, dict):
        meta = {}
    meta["content"] = body
    return meta


def wait_for_qdrant(url: str, *, timeout_s: float, api_key: str | None) -> None:
    client = QdrantClient(url=url, api_key=api_key)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            client.get_collections()
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("Qdrant did not become ready in time") from None
            time.sleep(1.0)


def main() -> None:
    settings = IndexerSettings()
    configure_logging(service="kb_indexer", level=settings.log_level)
    log = get_logger()

    base = Path(settings.kb_base_dir)
    if not base.exists():
        raise RuntimeError(f"KB base dir does not exist: {base}")
    roots = [base]
    log.info("kb_tree_found", path=str(base))

    ops = Path(settings.ops_dir)
    if ops.exists():
        roots.append(ops)
        log.info("kb_tree_found", path=str(ops))

    log.info("qdrant_wait_start", qdrant_url=settings.qdrant_url)
    wait_for_qdrant(
        settings.qdrant_url,
        timeout_s=settings.qdrant_startup_timeout_s,
        api_key=settings.qdrant_api_key,
    )
    log.info("qdrant_ready", qdrant_url=settings.qdrant_url)

    emb = DeterministicEmbeddingProvider(vector_dim=settings.kb_vector_dim)
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        embedding_provider=emb,
        vector_dim=settings.kb_vector_dim,
    )

    docs = []
    for root in roots:
        root_docs = load_documents_from_tree(root)
        log.info("docs_loaded", root=str(root), doc_count=len(root_docs))
        docs.extend(root_docs)

    store.upsert_documents(docs)
    log.info("index_complete", doc_count=len(docs))


if __name__ == "__main__":
    main()
