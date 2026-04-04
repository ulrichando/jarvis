"""Skills change detection and command list refresh."""

from __future__ import annotations

from typing import Any, Callable, List, Optional


class SkillsChangeWatcher:
    """Keeps the commands list fresh when skills change on disk.

    Handles:
    1. Skill file changes (watcher) - full cache clear + disk re-scan
    2. Feature flag refresh - memo-only clear for isEnabled() predicates

    Equivalent to useSkillsChange React hook.
    """

    def __init__(
        self,
        cwd: Optional[str],
        on_commands_change: Callable,
        get_commands: Callable,
        clear_commands_cache: Callable,
        clear_memoization_caches: Callable,
        skill_change_subscribe: Callable,
        feature_flag_subscribe: Optional[Callable] = None,
    ):
        self._cwd = cwd
        self._on_commands_change = on_commands_change
        self._get_commands = get_commands
        self._clear_commands_cache = clear_commands_cache
        self._clear_memoization_caches = clear_memoization_caches
        self._unsubscribe_skill: Optional[Callable] = None
        self._unsubscribe_flag: Optional[Callable] = None

        # Subscribe to skill changes
        self._unsubscribe_skill = skill_change_subscribe(self._handle_change)
        if feature_flag_subscribe:
            self._unsubscribe_flag = feature_flag_subscribe(self._handle_flag_refresh)

    async def _handle_change(self) -> None:
        if not self._cwd:
            return
        try:
            self._clear_commands_cache()
            commands = await self._get_commands(self._cwd)
            self._on_commands_change(commands)
        except Exception:
            pass

    async def _handle_flag_refresh(self) -> None:
        if not self._cwd:
            return
        try:
            self._clear_memoization_caches()
            commands = await self._get_commands(self._cwd)
            self._on_commands_change(commands)
        except Exception:
            pass

    def dispose(self) -> None:
        if self._unsubscribe_skill:
            self._unsubscribe_skill()
        if self._unsubscribe_flag:
            self._unsubscribe_flag()
