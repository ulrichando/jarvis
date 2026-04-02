"""JARVIS Terminal Shell — the first interactive interface."""

import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.table import Table

from brain.main import Brain
from brain.reasoning.persona import JARVIS_GREETING


console = Console()


def print_jarvis(text: str):
    """Print JARVIS output with styling."""
    console.print(
        Panel(
            Markdown(text),
            title="[bold cyan]JARVIS[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
    )


def print_banner():
    """Print the JARVIS startup banner."""
    banner = Text()
    banner.append("    ╦╔═╗╦═╗╦  ╦╦╔═╗\n", style="bold cyan")
    banner.append("    ║╠═╣╠╦╝╚╗╔╝║╚═╗\n", style="bold cyan")
    banner.append("   ╚╝╩ ╩╩╚═ ╚╝ ╩╚═╝\n", style="bold cyan")
    banner.append("  Self-Evolving AI Brain\n", style="dim")
    banner.append("  v0.1.0 — Phase 1 MVP\n", style="dim")
    console.print(banner)


def print_help():
    """Print available commands."""
    table = Table(title="Commands", border_style="cyan")
    table.add_column("Command", style="bold green")
    table.add_column("Description")
    table.add_row("/learn <fact>", "Teach JARVIS a new fact")
    table.add_row("/recall <query>", "Search JARVIS's memory")
    table.add_row("/stats", "Show brain statistics")
    table.add_row("/maintain", "Run memory maintenance (decay, prune, compress)")
    table.add_row("/help", "Show this help")
    table.add_row("clear", "Clear the screen")
    table.add_row("exit", "Shut down JARVIS")
    console.print(table)


def print_memories(memories: list[dict]):
    """Display recalled memories."""
    if not memories:
        print_jarvis("No relevant memories found.")
        return

    table = Table(title="Recalled Memories", border_style="cyan")
    table.add_column("Strength", justify="center", width=10)
    table.add_column("Type", width=10)
    table.add_column("Content")
    table.add_column("Accesses", justify="center", width=8)

    for mem in memories:
        strength = mem["strength"]
        bar = "█" * int(strength * 10)
        color = "green" if strength > 0.7 else "yellow" if strength > 0.3 else "red"
        table.add_row(
            f"[{color}]{bar:<10}[/{color}]",
            mem["type"],
            mem["content"][:80],
            str(mem["access_count"]),
        )

    console.print(table)


def print_stats(stats: dict):
    """Display brain statistics."""
    lattice = stats["lattice"]
    table = Table(title="Brain Statistics", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Conversations logged", str(stats["conversations"]))
    table.add_row("Memory nodes (alive)", f"{lattice['alive_nodes']} / {lattice['total_nodes']}")
    table.add_row("Strong memories", str(lattice["strong_nodes"]))
    table.add_row("Synapses (alive)", f"{lattice['alive_synapses']} / {lattice['total_synapses']}")
    table.add_row("Concepts formed", str(lattice["concepts"]))
    table.add_row("Disk size", stats["disk_size"])

    console.print(table)


async def handle_command(brain: Brain, command: str) -> bool:
    """Handle special commands. Returns True if handled."""
    if command.startswith("/learn "):
        fact = command[7:].strip()
        if fact:
            result = brain.learn(fact)
            print_jarvis(result)
        return True

    if command.startswith("/recall "):
        query = command[8:].strip()
        if query:
            memories = brain.remember(query)
            print_memories(memories)
        return True

    if command == "/stats":
        print_stats(brain.brain_stats())
        return True

    if command == "/maintain":
        with console.status("[cyan]Running maintenance...[/cyan]", spinner="dots"):
            result = brain.memory.maintain()
        print_jarvis(
            f"Maintenance complete. "
            f"Pruned: {result['pruned']} dead memories. "
            f"New concepts: {result['new_concepts']}. "
            f"Alive nodes: {result['stats']['alive_nodes']}."
        )
        return True

    if command == "/help":
        print_help()
        return True

    return False


async def run_shell():
    """Run the interactive terminal shell."""
    print_banner()

    brain = Brain()
    await brain.start()

    print_jarvis(JARVIS_GREETING)

    while True:
        try:
            console.print()
            user_input = console.input("[bold green]You>[/bold green] ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "bye"):
                print_jarvis("Shutting down. Until next time, sir.")
                await brain.shutdown()
                break

            if user_input.lower() == "clear":
                console.clear()
                continue

            # Check for special commands
            if await handle_command(brain, user_input):
                continue

            # Think and respond
            with console.status("[cyan]Thinking...[/cyan]", spinner="dots"):
                response = await brain.think(user_input)

            print_jarvis(response)

        except KeyboardInterrupt:
            console.print()
            print_jarvis("Interrupted. Shutting down gracefully.")
            await brain.shutdown()
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def main():
    """Entry point."""
    asyncio.run(run_shell())


if __name__ == "__main__":
    main()
