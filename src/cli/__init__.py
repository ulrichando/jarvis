"""JARVIS CLI shell -- terminal interface with Claude Code visual standards.

Submodules:
    display         Tool call/result formatting, diff display, token footer
    jarvis_cli      Main interactive CLI (run with `jarvis` or `python -m src.cli`)
    companion       F.R.I.D.A.Y. companion buddy
    exit            Clean exit helpers
    ndjson          NDJSON-safe serialization
    print_mode      Non-interactive single-query mode
    remoteIO        Remote I/O for bridge/SDK
    structuredIO    Structured NDJSON I/O for SDK mode
    update          Update checking
    handlers/       Subcommand handlers (agents, auth, autoMode, plugins)
    transports/     Transport layer (SSE, WebSocket, Hybrid, CCR)
"""
