from __future__ import annotations

from abc import ABC, abstractmethod

from debug_service.models import DebugStep


class DebugAdapter(ABC):
    """Strategy interface for language-specific debuggers."""

    @abstractmethod
    def debug(self, code: str) -> list[DebugStep]:
        """Execute code under a debugger and return a per-line trace."""
