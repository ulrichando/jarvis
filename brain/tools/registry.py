"""
Central tool registry. All tool definitions live here.
Only the specific tool needed per request is sent to Claude — never the full list.
"""

TOOLS: list[dict] = [
    {
        "name": "get_weather",
        "description": (
            "Fetch real-time weather data for a city. "
            "CALL when user asks about current weather, temperature, rain, forecast, "
            "or whether to bring a jacket or umbrella. "
            "DO NOT call for historical weather, climate questions, or if no city can be determined. "
            "If no city is mentioned, infer from user's home location (Yaoundé, Cameroon)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name. Infer from context or use default 'Yaoundé' if unspecified.",
                },
                "units": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit. Default: celsius.",
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "play_music",
        "description": (
            "Play music or audio on the user's system. "
            "CALL when user asks to play a song, artist, genre, or playlist. "
            "DO NOT call for general questions about music or lyrics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song name, artist, genre, or mood. E.g. 'jazz', 'Drake', 'chill beats'.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_reminder",
        "description": (
            "Set a reminder or alarm for the user. "
            "CALL when user asks to be reminded about something at a time. "
            "DO NOT call if no time or task is specified — ask the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What to remind the user about.",
                },
                "time": {
                    "type": "string",
                    "description": "When to remind. ISO 8601 or natural language like '3pm', 'in 30 minutes'.",
                },
            },
            "required": ["task", "time"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web for current or real-time information. "
            "CALL when user asks about news, recent events, or anything requiring up-to-date data. "
            "DO NOT call for general knowledge questions answerable from training data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Be specific. E.g. 'Pretva ride-hailing Cameroon 2026'.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_app",
        "description": (
            "Open or launch an application on the user's system. "
            "CALL when user says open, launch, or start followed by an app name. "
            "DO NOT call for general questions about applications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the application to open. E.g. 'Chrome', 'Terminal', 'Spotify'.",
                },
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "system_control",
        "description": (
            "Control system settings such as volume, brightness, or power state. "
            "CALL when user asks to adjust volume, brightness, mute, take a screenshot, "
            "lock screen, shutdown, restart, or sleep. "
            "DO NOT call for questions about system settings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "volume_up", "volume_down", "mute", "unmute",
                        "brightness_up", "brightness_down",
                        "screenshot", "lock", "shutdown", "restart", "sleep",
                    ],
                    "description": "The system action to perform.",
                },
                "value": {
                    "type": "integer",
                    "description": "Optional numeric value (e.g. volume level 0-100).",
                },
            },
            "required": ["action"],
        },
    },
]


def get_tools_for_request(tool_name: str | None) -> list[dict]:
    """
    Return only the single tool definition needed for this request.
    Never pass the full tool list to the model — only the relevant one.
    Returns an empty list if no tool is needed.
    """
    if tool_name is None:
        return []
    return [t for t in TOOLS if t["name"] == tool_name]
