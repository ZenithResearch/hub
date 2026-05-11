from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONTEXT_DIR = Path("/hub_context")


@dataclass(frozen=True)
class RuntimeContext:
    """Prompt context mounted by the external orchestrator."""

    soul: str = ""
    memory: str = ""
    user: str = ""

    def as_user_prompt(self) -> str:
        sections: list[str] = []
        if self.memory.strip():
            sections.append(f"Initial context:\n{self.memory.strip()}")
        if self.user.strip():
            sections.append(f"Task:\n{self.user.strip()}")
        return "\n\n".join(sections).strip()


def _read_context_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_context(context_dir: Path | str = DEFAULT_CONTEXT_DIR) -> RuntimeContext:
    context_path = Path(context_dir)
    return RuntimeContext(
        soul=_read_context_file(context_path / "SOUL.md"),
        memory=_read_context_file(context_path / "MEMORY.md"),
        user=_read_context_file(context_path / "USER.md"),
    )
