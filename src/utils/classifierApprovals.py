"""Tracks which tool uses were auto-approved by classifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

ClassifierType = Literal["bash", "auto-mode"]


@dataclass
class ClassifierApproval:
    classifier: ClassifierType
    matched_rule: Optional[str] = None
    reason: Optional[str] = None


_classifier_approvals: dict[str, ClassifierApproval] = {}
_classifier_checking: set[str] = set()
_checking_subscribers: list[Callable[[], None]] = []


def _emit_checking() -> None:
    for cb in _checking_subscribers:
        cb()


def set_classifier_approval(tool_use_id: str, matched_rule: str) -> None:
    _classifier_approvals[tool_use_id] = ClassifierApproval(
        classifier="bash", matched_rule=matched_rule
    )


def get_classifier_approval(tool_use_id: str) -> Optional[str]:
    approval = _classifier_approvals.get(tool_use_id)
    if not approval or approval.classifier != "bash":
        return None
    return approval.matched_rule


def set_yolo_classifier_approval(tool_use_id: str, reason: str) -> None:
    _classifier_approvals[tool_use_id] = ClassifierApproval(
        classifier="auto-mode", reason=reason
    )


def get_yolo_classifier_approval(tool_use_id: str) -> Optional[str]:
    approval = _classifier_approvals.get(tool_use_id)
    if not approval or approval.classifier != "auto-mode":
        return None
    return approval.reason


def set_classifier_checking(tool_use_id: str) -> None:
    _classifier_checking.add(tool_use_id)
    _emit_checking()


def clear_classifier_checking(tool_use_id: str) -> None:
    _classifier_checking.discard(tool_use_id)
    _emit_checking()


def subscribe_classifier_checking(callback: Callable[[], None]) -> Callable[[], None]:
    _checking_subscribers.append(callback)

    def unsubscribe() -> None:
        if callback in _checking_subscribers:
            _checking_subscribers.remove(callback)

    return unsubscribe


def is_classifier_checking(tool_use_id: str) -> bool:
    return tool_use_id in _classifier_checking


def delete_classifier_approval(tool_use_id: str) -> None:
    _classifier_approvals.pop(tool_use_id, None)


def clear_classifier_approvals() -> None:
    _classifier_approvals.clear()
    _classifier_checking.clear()
    _emit_checking()
