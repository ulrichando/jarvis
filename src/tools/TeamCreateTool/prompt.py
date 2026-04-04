"""Prompt for the TeamCreateTool."""
from __future__ import annotations


def get_prompt() -> str:
    return """
# TeamCreate

## When to Use

Use this tool proactively whenever:
- The user explicitly asks to use a team, swarm, or group of agents
- The user mentions wanting agents to work together, coordinate, or collaborate
- A task is complex enough that it would benefit from parallel work by multiple agents

When in doubt about whether a task warrants a team, prefer spawning a team.

Create a new team to coordinate multiple agents working on a project. Teams have a 1:1 correspondence with task lists (Team = TaskList).

```
{
  "team_name": "my-project",
  "description": "Working on feature X"
}
```

This creates:
- A team file at `~/.jarvis/teams/{team-name}/config.json`
- A corresponding task list directory at `~/.jarvis/tasks/{team-name}/`

## Team Workflow

1. **Create a team** with TeamCreate - this creates both the team and its task list
2. **Create tasks** using the Task tools (TaskCreate, TaskList, etc.)
3. **Spawn teammates** using the Agent tool with `team_name` and `name` parameters
4. **Assign tasks** using TaskUpdate with `owner` to give tasks to idle teammates
5. **Teammates work on assigned tasks** and mark them completed via TaskUpdate
6. **Shutdown your team** - when the task is completed, gracefully shut down teammates

## Task Ownership

Tasks are assigned using TaskUpdate with the `owner` parameter. Any agent can set or change task ownership via TaskUpdate.
""".strip()
