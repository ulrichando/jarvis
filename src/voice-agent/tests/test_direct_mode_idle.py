import os
import pytest
from direct_mode_idle import should_revert, idle_timeout_s


def test_disabled_when_timeout_zero():
    assert should_revert(idle_s=9999, timeout_s=0, tool_running=False) is False

def test_blocked_while_tool_running():
    assert should_revert(idle_s=9999, timeout_s=300, tool_running=True) is False

def test_no_revert_before_timeout():
    assert should_revert(idle_s=120, timeout_s=300, tool_running=False) is False

def test_no_revert_at_exact_timeout():
    assert should_revert(idle_s=300, timeout_s=300, tool_running=False) is False

def test_revert_past_timeout():
    assert should_revert(idle_s=301, timeout_s=300, tool_running=False) is True

def test_idle_timeout_default(monkeypatch):
    monkeypatch.delenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", raising=False)
    assert idle_timeout_s() == 300.0

def test_idle_timeout_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", "120")
    assert idle_timeout_s() == 120.0

def test_idle_timeout_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", "not-a-number")
    assert idle_timeout_s() == 300.0
