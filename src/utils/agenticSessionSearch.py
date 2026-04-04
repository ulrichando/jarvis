"""Agentic session search using LLM to find relevant sessions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

# Limits for transcript extraction
MAX_TRANSCRIPT_CHARS = 2000
MAX_MESSAGES_TO_SCAN = 100
MAX_SESSIONS_TO_SEARCH = 100

SESSION_SEARCH_SYSTEM_PROMPT = """Your goal is to find relevant sessions based on a user's search query.

You will be given a list of sessions with their metadata and a search query. Identify which sessions are most relevant to the query.

Each session may include:
- Title (display name or custom title)
- Tag (user-assigned category, shown as [tag: name] - users tag sessions with /tag command to categorize them)
- Branch (git branch name, shown as [branch: name])
- Summary (AI-generated summary)
- First message (beginning of the conversation)
- Transcript (excerpt of conversation content)

IMPORTANT: Tags are user-assigned labels that indicate the session's topic or category. If the query matches a tag exactly or partially, those sessions should be highly prioritized.

For each session, consider (in order of priority):
1. Exact tag matches (highest priority - user explicitly categorized this session)
2. Partial tag matches or tag-related terms
3. Title matches (custom titles or first message content)
4. Branch name matches
5. Summary and transcript content matches
6. Semantic similarity and related concepts

CRITICAL: Be VERY inclusive in your matching. Include sessions that:
- Contain the query term anywhere in any field
- Are semantically related to the query (e.g., "testing" matches sessions about "tests", "unit tests", "QA", etc.)
- Discuss topics that could be related to the query
- Have transcripts that mention the concept even in passing

When in doubt, INCLUDE the session. It's better to return too many results than too few. The user can easily scan through results, but missing relevant sessions is frustrating.

Return sessions ordered by relevance (most relevant first). If truly no sessions have ANY connection to the query, return an empty array - but this should be rare.

Respond with ONLY the JSON object, no markdown formatting:
{"relevant_indices": [2, 5, 0]}"""


@dataclass
class AgenticSearchResult:
    relevant_indices: list[int]


@dataclass
class SerializedMessage:
    type: str
    message: Optional[dict[str, Any]] = None


@dataclass
class LogOption:
    custom_title: Optional[str] = None
    tag: Optional[str] = None
    git_branch: Optional[str] = None
    summary: Optional[str] = None
    first_prompt: Optional[str] = None
    messages: Optional[list[SerializedMessage]] = None
    display_title: str = ""


def extract_message_text(message: SerializedMessage) -> str:
    """Extracts searchable text content from a message."""
    if message.type not in ("user", "assistant"):
        return ""

    content = message.message.get("content") if message.message else None
    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and "text" in block and isinstance(block["text"], str):
                texts.append(block["text"])
        return " ".join(t for t in texts if t)

    return ""


def extract_transcript(messages: list[SerializedMessage]) -> str:
    """Extracts a truncated transcript from session messages."""
    if not messages:
        return ""

    if len(messages) <= MAX_MESSAGES_TO_SCAN:
        messages_to_scan = messages
    else:
        half = MAX_MESSAGES_TO_SCAN // 2
        messages_to_scan = messages[:half] + messages[-half:]

    text = " ".join(
        t for t in (extract_message_text(m) for m in messages_to_scan) if t
    )
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > MAX_TRANSCRIPT_CHARS:
        return text[:MAX_TRANSCRIPT_CHARS] + "..."
    return text


def log_contains_query(log: LogOption, query_lower: str) -> bool:
    """Checks if a log contains the query term in any searchable field."""
    if query_lower in log.display_title.lower():
        return True
    if log.custom_title and query_lower in log.custom_title.lower():
        return True
    if log.tag and query_lower in log.tag.lower():
        return True
    if log.git_branch and query_lower in log.git_branch.lower():
        return True
    if log.summary and query_lower in log.summary.lower():
        return True
    if log.first_prompt and query_lower in log.first_prompt.lower():
        return True
    if log.messages and len(log.messages) > 0:
        transcript = extract_transcript(log.messages).lower()
        if query_lower in transcript:
            return True
    return False


async def agentic_session_search(
    query: str,
    logs: list[LogOption],
) -> list[LogOption]:
    """Performs an agentic search using LLM to find relevant sessions."""
    if not query.strip() or not logs:
        return []

    query_lower = query.lower()

    matching_logs = [log for log in logs if log_contains_query(log, query_lower)]

    if len(matching_logs) >= MAX_SESSIONS_TO_SEARCH:
        logs_to_search = matching_logs[:MAX_SESSIONS_TO_SEARCH]
    else:
        non_matching = [log for log in logs if not log_contains_query(log, query_lower)]
        remaining_slots = MAX_SESSIONS_TO_SEARCH - len(matching_logs)
        logs_to_search = matching_logs + non_matching[:remaining_slots]

    session_parts = []
    for index, log in enumerate(logs_to_search):
        parts = [f"{index}:"]
        parts.append(log.display_title)

        if log.custom_title and log.custom_title != log.display_title:
            parts.append(f"[custom title: {log.custom_title}]")
        if log.tag:
            parts.append(f"[tag: {log.tag}]")
        if log.git_branch:
            parts.append(f"[branch: {log.git_branch}]")
        if log.summary:
            parts.append(f"- Summary: {log.summary}")
        if log.first_prompt and log.first_prompt != "No prompt":
            parts.append(f"- First message: {log.first_prompt[:300]}")
        if log.messages and len(log.messages) > 0:
            transcript = extract_transcript(log.messages)
            if transcript:
                parts.append(f"- Transcript: {transcript}")

        session_parts.append(" ".join(parts))

    session_list = "\n".join(session_parts)
    user_message = f'Sessions:\n{session_list}\n\nSearch query: "{query}"\n\nFind the sessions that are most relevant to this query.'

    try:
        # Placeholder for LLM query - adapt to your provider system
        # response = await side_query(model, SESSION_SEARCH_SYSTEM_PROMPT, user_message)
        # For now, fall back to simple matching
        return matching_logs
    except Exception:
        return []
