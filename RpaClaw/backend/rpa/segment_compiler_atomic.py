from typing import Any, Dict, Optional

from backend.rpa.intent_models import IntentPlan
from backend.rpa.segment_models import SegmentSpec


def compile_atomic_segment(_intent: IntentPlan, _goal: str, _snapshot: Dict[str, Any]) -> Optional[SegmentSpec]:
    return None
