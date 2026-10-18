"""
Speaker volume by voice ("Lauter bitte", "Leiser bitte").
The LLM calls change_volume when it decides it has been asked to speak louder or quieter (function calling).
The volume is kept while puppie_main.py runs. After a restart it goes back to PUPPIE_VOLUME.
"""
import json
import os

# Volume (0.0-1.0). Adjust the starting volume with e.g. PUPPIE_VOLUME=0.5 in .env
level = float(os.getenv("PUPPIE_VOLUME", "0.7"))
STEP = 0.15
MIN_LEVEL = 0.1  # never go fully silent, or Puppie could not be heard asking to be louder
MAX_LEVEL = 1.0

# Description of the tool passed to the LLM
TOOLS = [{
    "type": "function",
    "function": {
        "name": "change_volume",
        "description": "Macht die eigene Stimme lauter oder leiser, wenn darum gebeten wird.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["lauter", "leiser"]},
                "steps": {
                    "type": "integer",
                    "description": "Nur für leiser: wie viele Stufen. 1 = etwas (Standard), 2 = deutlich, 3 = viel, "
                                   "6 = ganz leise. Lauter geht immer nur eine Stufe.",
                },
            },
            "required": ["direction"],
        },
    },
}]


def change_volume(direction, steps=1):
    """Change the volume by the given number of steps and return the result as text (in German) to pass to the LLM"""
    global level
    try:
        steps = max(1, min(6, int(steps)))
    except (TypeError, ValueError):
        steps = 1
    if direction == "lauter":
        if level >= MAX_LEVEL:
            return "Die Lautstärke ist schon am höchsten."
        # Only one step at a time, so a sudden loud voice does not startle anyone. Ask again for more
        level = min(MAX_LEVEL, level + STEP)
    elif direction == "leiser":
        if level <= MIN_LEVEL:
            return "Die Lautstärke ist schon am niedrigsten."
        level = max(MIN_LEVEL, level - STEP * steps)
    else:
        return "Unbekannte Richtung."
    print(f"🔊 Volume: {level:.2f}")
    return f"Die Lautstärke ist jetzt {round(level * 100)} Prozent."


def call_tool(name, arguments_json):
    """Run a call from the LLM and return the resulting text"""
    if name != "change_volume":
        return "Unbekanntes Werkzeug."
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        arguments = {}
    return change_volume(arguments.get("direction"), arguments.get("steps", 1))
