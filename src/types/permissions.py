"""
Python equivalent of permissions.ts

Pure permission type definitions -- no runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    Generic,
    List,
    Literal,
    Mapping,
    Optional,
    TypeVar,
    Union,
)

# Forward-reference aliases
ContentBlockParam = Any

# ============================================================================
# Permission Modes
# ============================================================================

EXTERNAL_PERMISSION_MODES = ("acceptEdits", "bypassPermissions", "default", "dontAsk", "plan")

ExternalPermissionMode = Literal["acceptEdits", "bypassPermissions", "default", "dontAsk", "plan"]
InternalPermissionMode = Union[ExternalPermissionMode, Literal["auto", "bubble"]]
PermissionMode = InternalPermissionMode

# Runtime validation set (without feature-flag gating in Python)
INTERNAL_PERMISSION_MODES = (*EXTERNAL_PERMISSION_MODES, "auto")
PERMISSION_MODES = INTERNAL_PERMISSION_MODES

# ============================================================================
# Permission Behaviors
# ============================================================================

PermissionBehavior = Literal["allow", "deny", "ask"]

# ============================================================================
# Permission Rules
# ============================================================================

PermissionRuleSource = Literal[
    "userSettings",
    "projectSettings",
    "localSettings",
    "flagSettings",
    "policySettings",
    "cliArg",
    "command",
    "session",
]


@dataclass
class PermissionRuleValue:
    """The value of a permission rule."""
    tool_name: str = ""
    rule_content: Optional[str] = None


@dataclass
class PermissionRule:
    """A permission rule with its source and behavior."""
    source: PermissionRuleSource = "session"
    rule_behavior: PermissionBehavior = "ask"
    rule_value: PermissionRuleValue = field(default_factory=PermissionRuleValue)


# ============================================================================
# Permission Updates
# ============================================================================

PermissionUpdateDestination = Literal[
    "userSettings", "projectSettings", "localSettings", "session", "cliArg"
]


@dataclass
class PermissionUpdateAddRules:
    type: Literal["addRules"] = "addRules"
    destination: PermissionUpdateDestination = "session"
    rules: List[PermissionRuleValue] = field(default_factory=list)
    behavior: PermissionBehavior = "ask"


@dataclass
class PermissionUpdateReplaceRules:
    type: Literal["replaceRules"] = "replaceRules"
    destination: PermissionUpdateDestination = "session"
    rules: List[PermissionRuleValue] = field(default_factory=list)
    behavior: PermissionBehavior = "ask"


@dataclass
class PermissionUpdateRemoveRules:
    type: Literal["removeRules"] = "removeRules"
    destination: PermissionUpdateDestination = "session"
    rules: List[PermissionRuleValue] = field(default_factory=list)
    behavior: PermissionBehavior = "ask"


@dataclass
class PermissionUpdateSetMode:
    type: Literal["setMode"] = "setMode"
    destination: PermissionUpdateDestination = "session"
    mode: ExternalPermissionMode = "default"


@dataclass
class PermissionUpdateAddDirectories:
    type: Literal["addDirectories"] = "addDirectories"
    destination: PermissionUpdateDestination = "session"
    directories: List[str] = field(default_factory=list)


@dataclass
class PermissionUpdateRemoveDirectories:
    type: Literal["removeDirectories"] = "removeDirectories"
    destination: PermissionUpdateDestination = "session"
    directories: List[str] = field(default_factory=list)


PermissionUpdate = Union[
    PermissionUpdateAddRules,
    PermissionUpdateReplaceRules,
    PermissionUpdateRemoveRules,
    PermissionUpdateSetMode,
    PermissionUpdateAddDirectories,
    PermissionUpdateRemoveDirectories,
]

WorkingDirectorySource = PermissionRuleSource


@dataclass
class AdditionalWorkingDirectory:
    """An additional directory included in permission scope."""
    path: str = ""
    source: WorkingDirectorySource = "session"


# ============================================================================
# Permission Decisions & Results
# ============================================================================

@dataclass
class PermissionCommandMetadata:
    """Minimal command shape for permission metadata."""
    name: str = ""
    description: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


PermissionMetadata = Optional[Dict[str, PermissionCommandMetadata]]

# Generic input type
Input = TypeVar("Input", bound=Dict[str, Any])


@dataclass
class PermissionAllowDecision:
    """Result when permission is granted."""
    behavior: Literal["allow"] = "allow"
    updated_input: Optional[Dict[str, Any]] = None
    user_modified: Optional[bool] = None
    decision_reason: Optional["PermissionDecisionReason"] = None
    tool_use_id: Optional[str] = None
    accept_feedback: Optional[str] = None
    content_blocks: Optional[List[ContentBlockParam]] = None


@dataclass
class PendingClassifierCheck:
    """Metadata for a pending classifier check."""
    command: str = ""
    cwd: str = ""
    descriptions: List[str] = field(default_factory=list)


@dataclass
class PermissionAskDecision:
    """Result when user should be prompted."""
    behavior: Literal["ask"] = "ask"
    message: str = ""
    updated_input: Optional[Dict[str, Any]] = None
    decision_reason: Optional["PermissionDecisionReason"] = None
    suggestions: Optional[List[PermissionUpdate]] = None
    blocked_path: Optional[str] = None
    metadata: Optional[PermissionMetadata] = None
    is_bash_security_check_for_misparsing: Optional[bool] = None
    pending_classifier_check: Optional[PendingClassifierCheck] = None
    content_blocks: Optional[List[ContentBlockParam]] = None


@dataclass
class PermissionDenyDecision:
    """Result when permission is denied."""
    behavior: Literal["deny"] = "deny"
    message: str = ""
    decision_reason: Optional["PermissionDecisionReason"] = None
    tool_use_id: Optional[str] = None


PermissionDecision = Union[
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionDenyDecision,
]


@dataclass
class PermissionPassthroughResult:
    """Permission result with passthrough behavior."""
    behavior: Literal["passthrough"] = "passthrough"
    message: str = ""
    decision_reason: Optional["PermissionDecisionReason"] = None
    suggestions: Optional[List[PermissionUpdate]] = None
    blocked_path: Optional[str] = None
    pending_classifier_check: Optional[PendingClassifierCheck] = None


PermissionResult = Union[PermissionDecision, PermissionPassthroughResult]


# ============================================================================
# PermissionDecisionReason
# ============================================================================

@dataclass
class PermissionDecisionReasonRule:
    type: Literal["rule"] = "rule"
    rule: PermissionRule = field(default_factory=PermissionRule)


@dataclass
class PermissionDecisionReasonMode:
    type: Literal["mode"] = "mode"
    mode: PermissionMode = "default"


@dataclass
class PermissionDecisionReasonSubcommandResults:
    type: Literal["subcommandResults"] = "subcommandResults"
    reasons: Dict[str, PermissionResult] = field(default_factory=dict)


@dataclass
class PermissionDecisionReasonPromptTool:
    type: Literal["permissionPromptTool"] = "permissionPromptTool"
    permission_prompt_tool_name: str = ""
    tool_result: Any = None


@dataclass
class PermissionDecisionReasonHook:
    type: Literal["hook"] = "hook"
    hook_name: str = ""
    hook_source: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class PermissionDecisionReasonAsyncAgent:
    type: Literal["asyncAgent"] = "asyncAgent"
    reason: str = ""


@dataclass
class PermissionDecisionReasonSandboxOverride:
    type: Literal["sandboxOverride"] = "sandboxOverride"
    reason: Literal["excludedCommand", "dangerouslyDisableSandbox"] = "excludedCommand"


@dataclass
class PermissionDecisionReasonClassifier:
    type: Literal["classifier"] = "classifier"
    classifier: str = ""
    reason: str = ""


@dataclass
class PermissionDecisionReasonWorkingDir:
    type: Literal["workingDir"] = "workingDir"
    reason: str = ""


@dataclass
class PermissionDecisionReasonSafetyCheck:
    type: Literal["safetyCheck"] = "safetyCheck"
    reason: str = ""
    classifier_approvable: bool = False


@dataclass
class PermissionDecisionReasonOther:
    type: Literal["other"] = "other"
    reason: str = ""


PermissionDecisionReason = Union[
    PermissionDecisionReasonRule,
    PermissionDecisionReasonMode,
    PermissionDecisionReasonSubcommandResults,
    PermissionDecisionReasonPromptTool,
    PermissionDecisionReasonHook,
    PermissionDecisionReasonAsyncAgent,
    PermissionDecisionReasonSandboxOverride,
    PermissionDecisionReasonClassifier,
    PermissionDecisionReasonWorkingDir,
    PermissionDecisionReasonSafetyCheck,
    PermissionDecisionReasonOther,
]


# ============================================================================
# Bash Classifier Types
# ============================================================================

@dataclass
class ClassifierResult:
    matches: bool = False
    matched_description: Optional[str] = None
    confidence: Literal["high", "medium", "low"] = "low"
    reason: str = ""


ClassifierBehavior = Literal["deny", "ask", "allow"]


@dataclass
class ClassifierUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class PromptLengths:
    system_prompt: int = 0
    tool_calls: int = 0
    user_prompts: int = 0


@dataclass
class YoloClassifierResult:
    thinking: Optional[str] = None
    should_block: bool = False
    reason: str = ""
    unavailable: Optional[bool] = None
    transcript_too_long: Optional[bool] = None
    model: str = ""
    usage: Optional[ClassifierUsage] = None
    duration_ms: Optional[int] = None
    prompt_lengths: Optional[PromptLengths] = None
    error_dump_path: Optional[str] = None
    stage: Optional[Literal["fast", "thinking"]] = None
    stage1_usage: Optional[ClassifierUsage] = None
    stage1_duration_ms: Optional[int] = None
    stage1_request_id: Optional[str] = None
    stage1_msg_id: Optional[str] = None
    stage2_usage: Optional[ClassifierUsage] = None
    stage2_duration_ms: Optional[int] = None
    stage2_request_id: Optional[str] = None
    stage2_msg_id: Optional[str] = None


# ============================================================================
# Permission Explainer Types
# ============================================================================

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class PermissionExplanation:
    risk_level: RiskLevel = "LOW"
    explanation: str = ""
    reasoning: str = ""
    risk: str = ""


# ============================================================================
# Tool Permission Context
# ============================================================================

ToolPermissionRulesBySource = Dict[str, List[str]]
# Keys are PermissionRuleSource values, values are rule strings.


@dataclass
class ToolPermissionContext:
    """Context needed for permission checking in tools."""
    mode: PermissionMode = "default"
    additional_working_directories: Dict[str, AdditionalWorkingDirectory] = field(default_factory=dict)
    always_allow_rules: ToolPermissionRulesBySource = field(default_factory=dict)
    always_deny_rules: ToolPermissionRulesBySource = field(default_factory=dict)
    always_ask_rules: ToolPermissionRulesBySource = field(default_factory=dict)
    is_bypass_permissions_mode_available: bool = False
    stripped_dangerous_rules: Optional[ToolPermissionRulesBySource] = None
    should_avoid_permission_prompts: Optional[bool] = None
    await_automated_checks_before_dialog: Optional[bool] = None
    pre_plan_mode: Optional[PermissionMode] = None
