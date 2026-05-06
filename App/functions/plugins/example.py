"""
Example plugin tools - demonstrates how to add new tools to A.V.A

Add your own tools here or create new files in this directory.
Tools are auto-discovered on startup.
"""

from core.tool_registry import tool


@tool(
    name="roll_die",
    description="Roll a die with a given range",
    params={
        "range": {
            "type": "integer",
            "description": "Number of sides on the die",
        }
    },
    required=["range"],
)
def roll_die(range: int) -> int:
    """Roll a die with the given range"""
    import random

    a = random.randint(1, range)
    return a
