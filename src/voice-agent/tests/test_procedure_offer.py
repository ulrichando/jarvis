"""Track 2.5 — end-of-turn procedure offer + user confirmation flow."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_derive_procedure_name_from_intent():
    """auto-derive 'deploy-app' from 'Jarvis, deploy the app'."""
    from jarvis_agent import _derive_procedure_name
    assert _derive_procedure_name("Jarvis, deploy the app") == "deploy-app"
    # set up + dev env — should derive set-up-dev
    name = _derive_procedure_name("can you set up the dev env")
    assert name and "set" in name and "up" in name
    name = _derive_procedure_name("find me a flight to Tokyo")
    assert name and "find" in name


def test_derive_procedure_name_returns_none_for_no_intent():
    from jarvis_agent import _derive_procedure_name
    assert _derive_procedure_name("what's the weather") is None
    assert _derive_procedure_name("") is None


def test_offer_phrase_format():
    """Offer phrase mentions the derived name + asks for confirmation."""
    from jarvis_agent import _build_offer_phrase
    phrase = _build_offer_phrase("deploy-app")
    assert "deploy-app" in phrase
    assert "?" in phrase  # it's a question


def test_confirmation_matches_yes_variants():
    from jarvis_agent import _is_procedure_confirmation
    assert _is_procedure_confirmation("yeah")
    assert _is_procedure_confirmation("yes save it")
    assert _is_procedure_confirmation("sure")
    assert _is_procedure_confirmation("ok do it")
    assert _is_procedure_confirmation("absolutely")
    assert not _is_procedure_confirmation("no thanks")
    assert not _is_procedure_confirmation("not now")
    assert not _is_procedure_confirmation("what's the weather")
    assert not _is_procedure_confirmation("")
