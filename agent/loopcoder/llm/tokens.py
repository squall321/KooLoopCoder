"""Token counting.

We don't have the exact tokenizer for every model, so we use a fast
approximation: tiktoken's cl100k base for OpenAI-like models, and a
character/4 heuristic as a fallback. Accurate enough for budget management.
"""

from __future__ import annotations

import functools
from typing import Iterable


class TokenCounter:
    """Approximate token counter usable for context budgeting."""

    def __init__(self, model: str = "default") -> None:
        self.model = model
        self._enc = self._load_encoding(model)

    @staticmethod
    @functools.lru_cache(maxsize=4)
    def _load_encoding(model: str):  # type: ignore[no-untyped-def]
        try:
            import tiktoken  # type: ignore[import-not-found]
        except ImportError:
            return None
        # cl100k_base covers GPT-4-class. Qwen/DeepSeek tokenizers differ but
        # for budgeting cl100k is within ~10% which is acceptable as we keep
        # a reserve_for_completion margin.
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text, disallowed_special=()))
        return max(1, len(text) // 4)

    def count_messages(self, messages: Iterable[dict]) -> int:
        """Approximate tokens for a list of OpenAI-style messages."""
        total = 0
        for m in messages:
            # Per-message overhead approximation
            total += 4
            for v in m.values():
                if isinstance(v, str):
                    total += self.count(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            total += self.count_messages([item])
                        else:
                            total += self.count(str(item))
                elif v is not None:
                    total += self.count(str(v))
        total += 2  # priming
        return total
