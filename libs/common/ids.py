from __future__ import annotations

import uuid


KB_DOC_NAMESPACE = uuid.UUID("d71b0f2c-1e3e-4d8d-9a3f-6df1c5c7f5c5")


def new_request_id() -> str:
    return str(uuid.uuid4())


def stable_uuid_for_kb_doc(doc_id: str) -> str:
    """Stable UUID string for a given KB doc id."""
    return str(uuid.uuid5(KB_DOC_NAMESPACE, doc_id))


def new_id(prefix: str = "") -> str:
    """Generate a unique ID with an optional prefix, e.g. 'job_<uuid4>'."""
    uid = str(uuid.uuid4())
    return f"{prefix}_{uid}" if prefix else uid

