"""Project-convention auto-loader (CC14).

Many projects ship instructions for AI coding agents in well-known files:
- ``CLAUDE.md`` (Claude Code)
- ``AGENTS.md`` (general)
- ``.loopcoderrc`` / ``.loopcoderrc.md`` (this project)
- ``CONTRIBUTING.md``
- ``README.md`` (last-resort)

The controller pins these into context automatically, behind any explicit
``pin_in_context`` from the plan, so user-authored instructions always
reach the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Files we auto-pin, in priority order. We stop at the first hit per "kind"
# but include all distinct file types we find.
CONVENTION_FILE_NAMES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    ".loopcoderrc.md",
    ".loopcoderrc",
    "CONTRIBUTING.md",
    "README.md",
)


@dataclass
class Convention:
    path: str           # workspace-relative
    content: str
    bytes: int


def load_conventions(workspace: str | Path, max_bytes_per_file: int = 64 * 1024) -> list[Convention]:
    """Find and read recognized convention files.

    Searches the workspace root and its top-level directories (``./docs/``,
    ``./.github/``). Returns at most one file per recognized name.
    """
    root = Path(workspace).resolve()
    found: dict[str, Convention] = {}
    candidates: list[Path] = [root]
    for sub in (".github", "docs", ".loopcoder"):
        p = root / sub
        if p.is_dir():
            candidates.append(p)

    for d in candidates:
        for name in CONVENTION_FILE_NAMES:
            if name in found:
                continue
            f = d / name
            if not f.is_file():
                continue
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            if len(text.encode()) > max_bytes_per_file:
                text = text[: max_bytes_per_file] + "\n[... truncated by loopcoder convention loader ...]"
            rel = str(f.relative_to(root))
            found[name] = Convention(path=rel, content=text, bytes=len(text.encode()))
    # Preserve declared priority order
    return [found[n] for n in CONVENTION_FILE_NAMES if n in found]
