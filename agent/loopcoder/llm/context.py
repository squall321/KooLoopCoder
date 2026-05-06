"""Context window manager (★ critical: full preservation strategy).

Priority of sections (highest first):
  1. System prompt (with tool defs)
  2. Current goal + acceptance
  3. Pinned files (always full text)
  4. Recent verify-failure logs (NEVER truncated)
  5. Git diff since goal start
  6. Recent attempts (tool calls + results)
  7. Workspace file tree
  8. Older attempts (compressed summaries only)

Budget management:
- We track total tokens via TokenCounter.
- When approaching budget, we drop sections in REVERSE priority order.
- Verify logs and pinned files are NEVER dropped or truncated.
- When even higher-priority sections wouldn't fit, we raise ContextOverflowError
  so the loop controller can decide (the user-friendly behavior is to abort the
  goal rather than silently truncate critical info).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from loopcoder.llm.tokens import TokenCounter


SectionKind = Literal[
    "system",
    "goal",
    "pinned_files",
    "verify_log",
    "git_diff",
    "attempt",
    "file_tree",
    "old_summary",
]

# Priority: lower number = MORE important, kept first.
PRIORITY: dict[SectionKind, int] = {
    "system": 0,
    "goal": 1,
    "verify_log": 2,
    "pinned_files": 3,
    "git_diff": 4,
    "attempt": 5,
    "file_tree": 6,
    "old_summary": 7,
}

# Sections that must NEVER be truncated / summarized.
NEVER_TRUNCATE: set[SectionKind] = {"system", "goal", "verify_log", "pinned_files"}


class ContextOverflowError(RuntimeError):
    """Raised when even the must-keep sections do not fit the budget."""


@dataclass
class ContextSection:
    kind: SectionKind
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None  # required when role == "tool"
    name: str | None = None  # tool name (for assistant tool-calls or tool replies)
    # Free-form metadata, preserved for inspection but not sent to LLM.
    meta: dict = field(default_factory=dict)

    def to_message(self) -> dict:
        msg: dict = {"role": self.role, "content": self.content}
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


@dataclass
class BudgetReport:
    used_tokens: int
    budget: int
    dropped: list[ContextSection] = field(default_factory=list)


class ContextBuilder:
    """Assemble the prompt for a single LLM call respecting budget rules."""

    def __init__(
        self,
        token_counter: TokenCounter,
        total_budget_tokens: int,
        reserve_for_completion: int,
    ) -> None:
        self._tokens = token_counter
        self._budget = total_budget_tokens - reserve_for_completion
        if self._budget <= 0:
            raise ValueError("budget must be > reserve_for_completion")
        self._sections: list[ContextSection] = []

    def add(self, section: ContextSection) -> None:
        self._sections.append(section)

    def add_many(self, sections: Iterable[ContextSection]) -> None:
        self._sections.extend(sections)

    # ----- packing -----

    def pack(self) -> tuple[list[dict], BudgetReport]:
        """Return (messages, report). Drop low-priority sections until under budget."""
        # Stable order: by priority then insertion order.
        ordered = sorted(
            enumerate(self._sections),
            key=lambda iv: (PRIORITY[iv[1].kind], iv[0]),
        )
        kept: list[ContextSection] = [s for _, s in ordered]
        dropped: list[ContextSection] = []

        while True:
            messages = [s.to_message() for s in kept]
            used = self._tokens.count_messages(messages)
            if used <= self._budget:
                # Restore original conversational order (insertion order)
                # but stable so verify_log etc. show up where appended.
                kept_sorted = sorted(kept, key=lambda s: self._sections.index(s))
                final_msgs = [s.to_message() for s in kept_sorted]
                return final_msgs, BudgetReport(used_tokens=used, budget=self._budget, dropped=dropped)
            # Drop the lowest-priority kept section that is allowed to drop.
            droppable = [s for s in kept if s.kind not in NEVER_TRUNCATE]
            if not droppable:
                raise ContextOverflowError(
                    f"context exceeds budget ({used} > {self._budget}) but only "
                    f"non-droppable sections remain ({len(kept)} sections)"
                )
            # Sort droppable by priority desc (drop the least important first).
            droppable.sort(key=lambda s: PRIORITY[s.kind], reverse=True)
            victim = droppable[0]
            kept.remove(victim)
            dropped.append(victim)
