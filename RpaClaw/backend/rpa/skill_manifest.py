from copy import deepcopy
from typing import Any


def build_manifest(
    skill_name: str,
    description: str,
    params: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "version": 2,
        "name": skill_name,
        "description": description,
        "goal": description,
        "params": deepcopy(params),
        "steps": deepcopy(steps),
    }
