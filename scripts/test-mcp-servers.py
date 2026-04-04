#!/usr/bin/env python3
"""Test all configured MCP servers — shows which ones connect and which fail."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.mcp.manager import MCPManager

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"


def main():
    print(f"\n{BOLD}JARVIS MCP Server Activation Test{RESET}\n")

    mgr = MCPManager()
    mgr.load_config()

    servers = mgr.list_servers()
    if not servers:
        print(f"{RED}No MCP servers configured.{RESET}")
        print("Check ~/.jarvis/mcp.json")
        return

    print(f"Found {len(servers)} servers configured. Testing connections...\n")

    results = []
    for srv in servers:
        name = srv["name"]
        print(f"  {CYAN}Connecting to {name}...{RESET}", end=" ", flush=True)

        ok = mgr.start_server(name) if not srv["running"] else True
        if ok:
            tools = [t for t in mgr.list_tools() if t["server"] == name]
            print(f"{GREEN}OK{RESET} — {len(tools)} tools")
            for t in tools[:5]:
                print(f"    - {t['name']}")
            if len(tools) > 5:
                print(f"    ... and {len(tools) - 5} more")
            results.append((name, True, len(tools), None))
        else:
            print(f"{RED}FAILED{RESET}")
            results.append((name, False, 0, "Connection failed"))

    print(f"\n{BOLD}Summary:{RESET}")
    print("─" * 50)
    ok_count = sum(1 for _, ok, _, _ in results if ok)
    fail_count = len(results) - ok_count

    for name, ok, tool_count, err in results:
        status = f"{GREEN}ACTIVE{RESET}" if ok else f"{RED}FAILED{RESET}"
        tools = f"{tool_count} tools" if ok else (err or "error")
        print(f"  {status}  {name:<20} {tools}")

    print("─" * 50)
    print(f"  {GREEN}{ok_count} active{RESET}  {RED}{fail_count} failed{RESET}")

    if fail_count:
        print(f"\n{YELLOW}Tip:{RESET} Failed servers likely need credentials.")
        print(f"  Edit: ~/.jarvis/.env.mcp")
        print(f"  Then: source ~/.jarvis/.env.mcp && python scripts/test-mcp-servers.py")

    mgr.stop_all()
    print()


if __name__ == "__main__":
    main()
