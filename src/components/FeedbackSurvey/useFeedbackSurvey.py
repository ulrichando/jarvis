"""
Converted from useFeedbackSurvey.tsx
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Literal, Callable, Awaitable

Message = type('Message', (), {})
FeedbackSurveyType = type('FeedbackSurveyType', (), {})
import asyncio
import re


@dataclass
class FeedbackSurveyConfig:
    minTimeBeforeFeedbackMs: int | float
    minTimeBetweenFeedbackMs: int | float
    minTimeBetweenGlobalFeedbackMs: int | float
    minUserTurnsBeforeFeedback: int | float
    minUserTurnsBetweenFeedback: int | float
    hideThanksAfterMs: int | float
    onForModels: list[str]
    probability: int | float


@dataclass
class TranscriptAskConfig:
    probability: int | float


DEFAULT_FEEDBACK_SURVEY_CONFIG = {
    'minTimeBeforeFeedbackMs': 600000,
    'minTimeBetweenFeedbackMs': 3600000,
    'minTimeBetweenGlobalFeedbackMs': 100000000,
    'minUserTurnsBeforeFeedback': 5,
    'minUserTurnsBetweenFeedback': 10,
    'hideThanksAfterMs': 3000,
    'onForModels': ['*'],
    'probability': 0.005,
}
DEFAULT_TRANSCRIPT_ASK_CONFIG = {
    'probability': 0,
}


# State: feedbackSurvey = (  (setter: setFeedbackSurvey)
# Ref: lastAssistantMessageIdRef = 'unknown'
# Ref: sessionStartTime = Date.now(
# Ref: submitCountAtSessionStart = submitCount
# Ref: submitCountRef = submitCount
# Ref: messagesRef = messages
# Ref: probabilityPassedRef = False
# Ref: lastEligibleSubmitCountRef = None
# Memoized: updateLastShownTime
# Memoized: onOpen
# Memoized: onSelect
# Memoized: shouldShowTranscriptPrompt
# Memoized: onTranscriptPromptShown
# Memoized: onTranscriptSelect
# Memoized: isModelAllowed
# Memoized: shouldOpen

def useFeedbackSurvey(messages: list[Message], isLoading: bool, submitCount: int | float, surveyType: FeedbackSurveyType = 'session', hasActivePrompt: bool = False) -> Any:
    state: 'closed' | 'open' | 'thanks' | 'transcript_prompt' | 'submitting' | 'submitted'
    lastResponse: FeedbackSurveyResponse | None
    handleSelect: lambda selected: FeedbackSurveyResponse
    handleTranscriptSelect: lambda selected: TranscriptShareResponse
