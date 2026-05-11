"""
Vault contact scanner.

Returns all Rolodex contacts (type: person or domain includes 'people').
Matrix IDs may be empty — callers use that to distinguish connected vs invite-able.

Supported matrix frontmatter keys:
  matrix_id: "@alice:localhost"
  matrix_ids: ["@alice:localhost", "@alice:matrix.org"]
  matrix: "@alice:localhost"

Display name precedence: name → title → filename stem.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml


@dataclass
class VaultContact:
    name: str
    matrix_ids: list[str] = field(default_factory=list)
    source_file: str = ""


def get_contacts(vault_path: str) -> list[VaultContact]:
    """
    Return all Rolodex contacts from the vault.
    A note qualifies if its frontmatter has type: person OR domain includes 'people'.
    matrix_ids may be an empty list — those contacts get an invite button in the UI.
    """
    if not vault_path or not os.path.isdir(vault_path):
        return []

    contacts: list[VaultContact] = []

    # Directories to skip — templates, ops scaffolding, sessions, git internals
    _SKIP_DIRS = {"templates", "ops", "self", "archive", ".git", ".obsidian"}

    for root, dirs, files in os.walk(vault_path):
        # Prune skip dirs in-place so os.walk doesn't descend into them
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            fm = _parse_frontmatter(content)
            if fm is None:
                continue

            if not _is_person(fm):
                continue

            name = fm.get("name") or fm.get("title") or os.path.splitext(fname)[0]
            ids = _extract_matrix_ids(fm)
            contacts.append(VaultContact(name=str(name), matrix_ids=ids, source_file=fpath))

    contacts.sort(key=lambda c: c.name.lower())
    return contacts


# ── Helpers ──────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.DOTALL)


def _parse_frontmatter(content: str) -> dict | None:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    try:
        result = yaml.safe_load(m.group(1))
        return result if isinstance(result, dict) else None
    except yaml.YAMLError:
        return None


def _is_person(fm: dict) -> bool:
    """True if the note represents a person in the Rolodex.

    Requires type: person (explicit) OR (domain includes 'people' AND type is absent/person).
    Notes with type: note that happen to tag domain: people are *about* people, not contacts.
    """
    note_type = fm.get("type")
    if note_type == "person":
        return True
    # Only fall through to domain check for notes without an explicit non-person type
    if note_type and note_type != "person":
        return False
    domain = fm.get("domain", [])
    if isinstance(domain, list):
        return "people" in domain
    if isinstance(domain, str):
        return "people" in domain
    return False


def _extract_matrix_ids(fm: dict) -> list[str]:
    raw = fm.get("matrix_id") or fm.get("matrix_ids") or fm.get("matrix")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw if str(v).startswith("@")]
    v = str(raw).strip().strip("\"'")
    return [v] if v.startswith("@") else []
