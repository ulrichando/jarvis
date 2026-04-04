"""
Python equivalent of logs.ts

Type definitions for session transcript entries and log options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

# Forward-reference aliases for external types
UUID = str
Message = Any
FileHistorySnapshot = Any
ContentReplacementRecord = Any
QueueOperationMessage = Any
AgentId = str  # from ids.py


# --------------------------------------------------------------------------
# SerializedMessage
# --------------------------------------------------------------------------

@dataclass
class SerializedMessage:
    """A message extended with serialization metadata."""
    cwd: str = ""
    user_type: str = ""
    entrypoint: Optional[str] = None
    session_id: str = ""
    timestamp: str = ""
    version: str = ""
    git_branch: Optional[str] = None
    slug: Optional[str] = None
    # Inherits all Message fields -- represented as a dict mixin in practice
    message: Message = None


# --------------------------------------------------------------------------
# PersistedWorktreeSession
# --------------------------------------------------------------------------

@dataclass
class PersistedWorktreeSession:
    """Worktree session state persisted to the transcript for resume."""
    original_cwd: str = ""
    worktree_path: str = ""
    worktree_name: str = ""
    worktree_branch: Optional[str] = None
    original_branch: Optional[str] = None
    original_head_commit: Optional[str] = None
    session_id: str = ""
    tmux_session_name: Optional[str] = None
    hook_based: Optional[bool] = None


# --------------------------------------------------------------------------
# LogOption
# --------------------------------------------------------------------------

@dataclass
class LogOption:
    date: str = ""
    messages: List[SerializedMessage] = field(default_factory=list)
    full_path: Optional[str] = None
    value: int = 0
    created: datetime = field(default_factory=datetime.now)
    modified: datetime = field(default_factory=datetime.now)
    first_prompt: str = ""
    message_count: int = 0
    file_size: Optional[int] = None
    is_sidechain: bool = False
    is_lite: Optional[bool] = None
    session_id: Optional[str] = None
    team_name: Optional[str] = None
    agent_name: Optional[str] = None
    agent_color: Optional[str] = None
    agent_setting: Optional[str] = None
    is_teammate: Optional[bool] = None
    leaf_uuid: Optional[UUID] = None
    summary: Optional[str] = None
    custom_title: Optional[str] = None
    tag: Optional[str] = None
    file_history_snapshots: Optional[List[FileHistorySnapshot]] = None
    attribution_snapshots: Optional[List["AttributionSnapshotMessage"]] = None
    context_collapse_commits: Optional[List["ContextCollapseCommitEntry"]] = None
    context_collapse_snapshot: Optional["ContextCollapseSnapshotEntry"] = None
    git_branch: Optional[str] = None
    project_path: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    pr_repository: Optional[str] = None
    mode: Optional[Literal["coordinator", "normal"]] = None
    worktree_session: Optional[PersistedWorktreeSession] = None
    content_replacements: Optional[List[ContentReplacementRecord]] = None


# --------------------------------------------------------------------------
# Transcript entry types
# --------------------------------------------------------------------------

@dataclass
class SummaryMessage:
    type: Literal["summary"] = "summary"
    leaf_uuid: UUID = ""
    summary: str = ""


@dataclass
class CustomTitleMessage:
    type: Literal["custom-title"] = "custom-title"
    session_id: UUID = ""
    custom_title: str = ""


@dataclass
class AiTitleMessage:
    type: Literal["ai-title"] = "ai-title"
    session_id: UUID = ""
    ai_title: str = ""


@dataclass
class LastPromptMessage:
    type: Literal["last-prompt"] = "last-prompt"
    session_id: UUID = ""
    last_prompt: str = ""


@dataclass
class TaskSummaryMessage:
    type: Literal["task-summary"] = "task-summary"
    session_id: UUID = ""
    summary: str = ""
    timestamp: str = ""


@dataclass
class TagMessage:
    type: Literal["tag"] = "tag"
    session_id: UUID = ""
    tag: str = ""


@dataclass
class AgentNameMessage:
    type: Literal["agent-name"] = "agent-name"
    session_id: UUID = ""
    agent_name: str = ""


@dataclass
class AgentColorMessage:
    type: Literal["agent-color"] = "agent-color"
    session_id: UUID = ""
    agent_color: str = ""


@dataclass
class AgentSettingMessage:
    type: Literal["agent-setting"] = "agent-setting"
    session_id: UUID = ""
    agent_setting: str = ""


@dataclass
class PRLinkMessage:
    type: Literal["pr-link"] = "pr-link"
    session_id: UUID = ""
    pr_number: int = 0
    pr_url: str = ""
    pr_repository: str = ""
    timestamp: str = ""


@dataclass
class ModeEntry:
    type: Literal["mode"] = "mode"
    session_id: UUID = ""
    mode: Literal["coordinator", "normal"] = "normal"


@dataclass
class WorktreeStateEntry:
    type: Literal["worktree-state"] = "worktree-state"
    session_id: UUID = ""
    worktree_session: Optional[PersistedWorktreeSession] = None


@dataclass
class ContentReplacementEntry:
    type: Literal["content-replacement"] = "content-replacement"
    session_id: UUID = ""
    agent_id: Optional[AgentId] = None
    replacements: List[ContentReplacementRecord] = field(default_factory=list)


@dataclass
class FileHistorySnapshotMessage:
    type: Literal["file-history-snapshot"] = "file-history-snapshot"
    message_id: UUID = ""
    snapshot: FileHistorySnapshot = None
    is_snapshot_update: bool = False


@dataclass
class FileAttributionState:
    """Per-file attribution state tracking JARVIS character contributions."""
    content_hash: str = ""
    claude_contribution: int = 0
    mtime: int = 0


@dataclass
class AttributionSnapshotMessage:
    type: Literal["attribution-snapshot"] = "attribution-snapshot"
    message_id: UUID = ""
    surface: str = ""
    file_states: Dict[str, FileAttributionState] = field(default_factory=dict)
    prompt_count: Optional[int] = None
    prompt_count_at_last_commit: Optional[int] = None
    permission_prompt_count: Optional[int] = None
    permission_prompt_count_at_last_commit: Optional[int] = None
    escape_count: Optional[int] = None
    escape_count_at_last_commit: Optional[int] = None


@dataclass
class TranscriptMessage:
    """SerializedMessage extended with transcript-specific fields."""
    parent_uuid: Optional[UUID] = None
    logical_parent_uuid: Optional[UUID] = None
    is_sidechain: bool = False
    git_branch: Optional[str] = None
    agent_id: Optional[str] = None
    team_name: Optional[str] = None
    agent_name: Optional[str] = None
    agent_color: Optional[str] = None
    prompt_id: Optional[str] = None
    # Plus all SerializedMessage fields
    message: SerializedMessage = None


@dataclass
class SpeculationAcceptMessage:
    type: Literal["speculation-accept"] = "speculation-accept"
    timestamp: str = ""
    time_saved_ms: int = 0


@dataclass
class ContextCollapseCommitEntry:
    type: Literal["marble-origami-commit"] = "marble-origami-commit"
    session_id: UUID = ""
    collapse_id: str = ""
    summary_uuid: str = ""
    summary_content: str = ""
    summary: str = ""
    first_archived_uuid: str = ""
    last_archived_uuid: str = ""


@dataclass
class StagedSpan:
    start_uuid: str = ""
    end_uuid: str = ""
    summary: str = ""
    risk: float = 0.0
    staged_at: int = 0


@dataclass
class ContextCollapseSnapshotEntry:
    type: Literal["marble-origami-snapshot"] = "marble-origami-snapshot"
    session_id: UUID = ""
    staged: List[StagedSpan] = field(default_factory=list)
    armed: bool = False
    last_spawn_tokens: int = 0


# --------------------------------------------------------------------------
# Entry union
# --------------------------------------------------------------------------

Entry = Union[
    TranscriptMessage,
    SummaryMessage,
    CustomTitleMessage,
    AiTitleMessage,
    LastPromptMessage,
    TaskSummaryMessage,
    TagMessage,
    AgentNameMessage,
    AgentColorMessage,
    AgentSettingMessage,
    PRLinkMessage,
    FileHistorySnapshotMessage,
    AttributionSnapshotMessage,
    QueueOperationMessage,
    SpeculationAcceptMessage,
    ModeEntry,
    WorktreeStateEntry,
    ContentReplacementEntry,
    ContextCollapseCommitEntry,
    ContextCollapseSnapshotEntry,
]


# --------------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------------

def sort_logs(logs: List[LogOption]) -> List[LogOption]:
    """Sort logs by modified date (newest first), then created date."""
    return sorted(
        logs,
        key=lambda log: (log.modified, log.created),
        reverse=True,
    )
