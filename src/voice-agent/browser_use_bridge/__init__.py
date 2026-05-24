"""Isolated browser_use bridge for the JARVIS voice-agent.

This package is DELIBERATELY a sibling of ``tools/`` (not inside it). The
voice-agent's tool discovery (``tools/registry.py::discover_builtin_tools``)
only globs ``tools/*.py`` non-recursively, so nothing here is ever imported
into the voice ``.venv`` at discovery time. That matters because
``runner.py`` imports ``browser_use`` — a package that exists ONLY in the
isolated venv at ``~/.jarvis/browser-use-venv`` and is intentionally absent
from the pinned voice ``.venv``. Importing it under the voice venv would
crash discovery.

``runner.py`` is executed as a standalone script BY the isolated venv's
Python interpreter, spawned as a subprocess from ``tools/browser.py``. It
must never be imported by the voice venv.
"""
