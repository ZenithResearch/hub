"""Tests for VaultWriteService (ISS-054)."""
from __future__ import annotations

import pytest
from pathlib import Path

from libs.common.vault_write import VaultWriteService


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Minimal vault scaffold: just capture/ so vault_connected() is True."""
    (tmp_path / "capture").mkdir()
    return tmp_path


def test_vault_connected(vault: Path) -> None:
    svc = VaultWriteService(str(vault))
    assert svc.vault_connected() is True


def test_vault_not_connected(tmp_path: Path) -> None:
    """vault_connected() is False when capture/ does not exist."""
    svc = VaultWriteService(str(tmp_path / "nonexistent"))
    assert svc.vault_connected() is False


def test_write_capture_creates_file(vault: Path) -> None:
    svc = VaultWriteService(str(vault))
    path = svc.write_capture(
        event_type="service-request",
        session_id="abcd1234",
        body="Test body content.",
    )
    assert path.exists()
    assert path.suffix == ".md"
    content = path.read_text()
    assert "event_type: service-request" in content
    assert "session_id: abcd1234" in content
    assert "type: capture" in content
    assert "Test body content." in content


def test_write_capture_subdirectory(vault: Path) -> None:
    svc = VaultWriteService(str(vault))
    path = svc.write_capture(
        event_type="session-close",
        session_id="abcd1234",
        body="Session ended.",
        subdirectory="sessions",
    )
    assert path.parent == vault / "capture" / "sessions"
    assert path.exists()


def test_write_capture_no_collision(vault: Path) -> None:
    """Second write with same event_type + session_id gets a -2 suffix."""
    svc = VaultWriteService(str(vault))
    p1 = svc.write_capture(event_type="service-request", session_id="abcd1234", body="first")
    p2 = svc.write_capture(event_type="service-request", session_id="abcd1234", body="second")
    assert p1 != p2
    assert p1.exists()
    assert p2.exists()


def test_write_capture_raises_when_disconnected(tmp_path: Path) -> None:
    svc = VaultWriteService(str(tmp_path / "no-vault"))
    with pytest.raises(RuntimeError, match="Vault not connected"):
        svc.write_capture(event_type="x", session_id="abcd", body="y")


def test_short_session_id(vault: Path) -> None:
    """session_id shorter than 4 chars is used as-is without crashing."""
    svc = VaultWriteService(str(vault))
    path = svc.write_capture(event_type="test", session_id="ab", body="ok")
    assert path.exists()
    assert "ab" in path.name


def test_custom_title(vault: Path) -> None:
    svc = VaultWriteService(str(vault))
    path = svc.write_capture(
        event_type="service-request",
        session_id="abcd1234",
        body="body",
        title="My Custom Title",
    )
    content = path.read_text()
    assert "# My Custom Title" in content
