"""VaultWriteService — writes Markdown capture files into the connected vault.

The hub writes only to its own co-located vault (same machine, local filesystem).
VAULT_PATH is a local path set in .env. Production sync to the operator's local
Obsidian is via the Forgejo git remote (ISS-050).
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _render_capture_file(
    *,
    event_type: str,
    session_id: str,
    title: str,
    body: str,
) -> str:
    """Render a vault-native Markdown capture file."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return (
        f"---\n"
        f"description: \"{event_type} receipt from hub session {session_id}\"\n"
        f"type: capture\n"
        f"source: hub\n"
        f"session_id: {session_id}\n"
        f"event_type: {event_type}\n"
        f"status: unprocessed\n"
        f"created: {now}\n"
        f"---\n"
        f"\n"
        f"# {title}\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"Source: hub session `{session_id}`\n"
    )


class VaultWriteService:
    """Writes capture files into the vault co-located with this hub.

    Args:
        vault_path: Absolute or relative path to the vault root. Corresponds
                    to the VAULT_PATH environment variable.
    """

    def __init__(self, vault_path: str) -> None:
        self.vault_root = Path(vault_path)

    def vault_connected(self) -> bool:
        """Return True if VAULT_PATH is set and capture/ exists."""
        return self.vault_root.exists() and (self.vault_root / "capture").exists()

    def write_capture(
        self,
        *,
        event_type: str,
        session_id: str,
        body: str,
        subdirectory: str = "",
        title: str = "",
    ) -> Path:
        """Write a Markdown capture file into capture/ (or a subdirectory).

        Args:
            event_type:   Short slug for the type of event (e.g. "service-request",
                          "session-output", "session-close").
            session_id:   UUID or opaque string for the originating hub session.
            body:         Markdown body — summary of the event, key outputs, links.
            subdirectory: Optional subdirectory under capture/ (e.g. "requests",
                          "sessions", "outputs"). Defaults to capture/ root.
            title:        Human-readable title. Defaults to
                          "{event_type} {short_id}".

        Returns:
            Path to the written file.

        Raises:
            RuntimeError: If the vault is not connected (capture/ does not exist).
        """
        if not self.vault_connected():
            raise RuntimeError(
                f"Vault not connected at {self.vault_root!r}. "
                "Set VAULT_PATH to a directory that contains capture/."
            )

        date_str = datetime.date.today().isoformat()
        short_id = session_id[:4] if len(session_id) >= 4 else session_id
        filename = f"{date_str}-{event_type}-{short_id}.md"

        resolved_title = title or f"{event_type} {short_id}"

        subdir_path = self.vault_root / "capture"
        if subdirectory:
            subdir_path = subdir_path / subdirectory
        subdir_path.mkdir(parents=True, exist_ok=True)

        # Avoid silent overwrites — suffix with -2, -3, … if the name is taken.
        target = subdir_path / filename
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 2
            while target.exists():
                target = subdir_path / f"{stem}-{counter}{suffix}"
                counter += 1

        content = _render_capture_file(
            event_type=event_type,
            session_id=session_id,
            title=resolved_title,
            body=body,
        )
        target.write_text(content, encoding="utf-8")
        log.info("vault_write", extra={"path": str(target), "event_type": event_type})
        return target
