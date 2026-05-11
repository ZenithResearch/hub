"""
Process Indexer — indexes hub process definitions into Qdrant for Frank's semantic matching.

Reads all .md files from PROCESS_DIR, extracts the title and "What this process does"
description, embeds them with fastembed, and upserts into the frank_processes collection.
Runs once at startup; re-run to pick up new or changed process files.

Environment variables:
  QDRANT_URL      http://qdrant:6333
  PROCESS_DIR     /hub/base/ops/processes
  LOG_LEVEL       info
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

log = logging.getLogger("process_indexer")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
PROCESS_DIR = Path(os.environ.get("PROCESS_DIR", "/hub/base/ops/processes"))
COLLECTION = "frank_processes"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_DIM = 384


_AD_HOC_PREFIX = re.compile(r"^msg_")


def extract_when_to_use(source: str) -> str:
    """Extract title + 'When to use' section, falling back to 'What this process does'."""
    title = ""
    section_lines: list[str] = []
    in_section = False
    target_headings = {"## When to use", "## What this process does"}

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## ") and not title:
            title = stripped[2:]
        elif stripped in target_headings and not in_section:
            in_section = True
        elif in_section:
            if stripped.startswith("## ") or stripped == "---":
                break
            section_lines.append(line)

    description = "\n".join(section_lines).strip()
    return f"{title}\n\n{description}" if description else title


def load_processes(process_dir: Path) -> list[dict]:
    """Load permanent process files only — skip index.md and ad-hoc msg_* files."""
    processes = []
    for path in sorted(process_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        if path.stem.startswith("NOTE"):
            continue  # documentation notes, not process definitions
        if _AD_HOC_PREFIX.match(path.stem):
            continue  # ad-hoc processes are single-use, not indexed
        source = path.read_text(encoding="utf-8")
        text = extract_when_to_use(source)
        if not text.strip():
            continue
        processes.append({
            "process_path": path.stem,
            "text": text,
            "source_path": str(path),
        })
    return processes


def index_processes() -> None:
    if not PROCESS_DIR.exists():
        log.warning("Process directory not found: %s", PROCESS_DIR)
        return

    processes = load_processes(PROCESS_DIR)
    if not processes:
        log.warning("No process files found in %s", PROCESS_DIR)
        return

    log.info("Indexing %d process(es) into Qdrant collection '%s'", len(processes), COLLECTION)

    client = QdrantClient(url=QDRANT_URL)

    # Recreate collection to ensure fresh index
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )

    # fastembed is bundled with qdrant-client[fastembed]
    from fastembed import TextEmbedding
    embedder = TextEmbedding(model_name=EMBED_MODEL)

    texts = [p["text"] for p in processes]
    vectors = list(embedder.embed(texts))

    points = [
        PointStruct(
            id=i,
            vector=list(vectors[i]),
            payload={
                "process_path": processes[i]["process_path"],
                "text": processes[i]["text"],
                "source_path": processes[i]["source_path"],
            },
        )
        for i in range(len(processes))
    ]

    client.upsert(collection_name=COLLECTION, points=points)
    log.info("Indexed %d processes", len(points))
    for p in processes:
        log.info("  %s", p["process_path"])


if __name__ == "__main__":
    index_processes()
