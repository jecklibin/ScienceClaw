from dataclasses import dataclass, field
from typing import Literal, Optional


GoalType = Literal["action", "read", "flow"]
ActionType = Literal["click", "open", "read", "fill", "download", "submit"]
RouteType = Literal["atomic", "selection", "flow"]
SelectionMode = Literal["direct", "first", "last", "nth", "max", "min", "latest", "earliest", "filter"]


@dataclass(slots=True)
class IntentTarget:
    scope: str = "page"
    role: str = ""
    semantic: str = ""


@dataclass(slots=True)
class IntentSelection:
    mode: SelectionMode = "direct"
    ordinal_index: Optional[int] = None
    value_source: str = ""
    metric: str = ""
    filter_operator: str = ""
    filter_value: str = ""


@dataclass(slots=True)
class IntentPlan:
    goal_type: GoalType
    action: ActionType
    route: RouteType
    target: IntentTarget = field(default_factory=IntentTarget)
    selection: IntentSelection = field(default_factory=IntentSelection)
    state_change: bool = False
    requires_reobserve: bool = False
    source_hint: str = ""
    result_key: str = ""
