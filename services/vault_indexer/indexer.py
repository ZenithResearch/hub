"""
Vault domain indexer.

Scans a vault for notes with `type: moc` and writes them into a SQLite FTS5
database at `{vault_path}/.index.db`.

Schema
------
domain_notes (regular table)
  id          TEXT PRIMARY KEY  — filename stem, e.g. "rolodex"
  path        TEXT              — absolute path to the .md file
  title       TEXT              — text of the first # heading
  description TEXT              — frontmatter `description` field
  links       TEXT              — space-separated wiki-link targets extracted
                                  from the body, e.g. "people agents orgs"
  body        TEXT              — full note body (after frontmatter)
  indexed_at  TEXT              — ISO-8601 timestamp

domain_notes_fts (FTS5 virtual table, content= domain_notes)
  Tokenises: id, title, description, body
  links is stored but not tokenised (used for graph traversal later)

Usage
-----
    from services.vault_indexer.indexer import build_index, search
    build_index("/Users/you/vault")
    results = search("/Users/you/vault", "rolodex")
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import NamedTuple


# ── Data types ───────────────────────────────────────────────────────────────

class DomainNote(NamedTuple):
    id: str           # filename stem
    path: str         # absolute path
    title: str
    description: str
    links: str        # space-separated wiki-link targets
    body: str         # full body after frontmatter


# ── Public API ────────────────────────────────────────────────────────────────

def build_index(vault_path: str) -> int:
    """
    Scan vault_path for type: moc notes, write to .index.db.
    Returns the number of notes indexed.
    """
    notes = _scan(vault_path)
    db_path = os.path.join(vault_path, ".index.db")
    _write(db_path, notes)
    return len(notes)


def search(vault_path: str, query: str, limit: int = 20) -> list[dict]:
    """
    Full-text search over indexed domain notes.
    Returns a list of dicts with id, title, description, links, rank.
    """
    db_path = os.path.join(vault_path, ".index.db")
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT dn.id, dn.title, dn.description, dn.links, fts.rank
        FROM domain_notes_fts fts
        JOIN domain_notes dn ON dn.id = fts.id
        WHERE domain_notes_fts MATCH ?
        ORDER BY fts.rank
        LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_links(vault_path: str, note_id: str) -> list[str]:
    """
    Return the list of wiki-link targets from a specific domain note.
    This is the entry point for graph traversal — not yet implemented beyond
    this lookup.
    """
    db_path = os.path.join(vault_path, ".index.db")
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT links FROM domain_notes WHERE id = ?", (note_id,)
    ).fetchone()
    con.close()
    if not row or not row[0]:
        return []
    return [l for l in row[0].split() if l]


# ── Scanner ───────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_WIKI_LINK_RE   = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+)?\]\]")
_HEADING_RE     = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _scan(vault_path: str) -> list[DomainNote]:
    notes: list[DomainNote] = []
    notes_dir = os.path.join(vault_path, "notes")
    if not os.path.isdir(notes_dir):
        # Fall back to scanning root
        notes_dir = vault_path

    for fname in os.listdir(notes_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(notes_dir, fname)
        try:
            content = open(fpath, encoding="utf-8").read()
        except OSError:
            continue

        fm = _parse_frontmatter(content)
        if fm.get("type") != "moc":
            continue

        stem = os.path.splitext(fname)[0]
        body = _FRONTMATTER_RE.sub("", content).strip()
        title = _first_heading(body) or stem
        description = fm.get("description", "")
        links = " ".join(_extract_links(body))

        notes.append(DomainNote(
            id=stem,
            path=fpath,
            title=title,
            description=description,
            links=links,
            body=body,
        ))

    return notes


# ── Writer ────────────────────────────────────────────────────────────────────

def _write(db_path: str, notes: list[DomainNote]) -> None:
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS domain_notes (
            id          TEXT PRIMARY KEY,
            path        TEXT NOT NULL,
            title       TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            links       TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL DEFAULT '',
            indexed_at  TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS domain_notes_fts
        USING fts5(
            id UNINDEXED,
            title,
            description,
            body,
            links UNINDEXED,
            content=domain_notes,
            content_rowid=rowid
        );
    """)

    now = datetime.now(timezone.utc).isoformat()

    for note in notes:
        con.execute(
            """
            INSERT INTO domain_notes (id, path, title, description, links, body, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path        = excluded.path,
                title       = excluded.title,
                description = excluded.description,
                links       = excluded.links,
                body        = excluded.body,
                indexed_at  = excluded.indexed_at
            """,
            (note.id, note.path, note.title, note.description,
             note.links, note.body, now),
        )

    # Rebuild FTS index from content table
    con.execute("INSERT INTO domain_notes_fts(domain_notes_fts) VALUES('rebuild')")
    con.commit()
    con.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> dict:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        result[key.strip()] = val.strip().strip("\"'")
    return result


def _first_heading(body: str) -> str:
    m = _HEADING_RE.search(body)
    return m.group(1).strip() if m else ""


def _extract_links(body: str) -> list[str]:
    """Extract unique wiki-link targets from note body."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKI_LINK_RE.finditer(body):
        target = m.group(1).strip()
        if target not in seen:
            seen.add(target)
            out.append(target)
    return out
