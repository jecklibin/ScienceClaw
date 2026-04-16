from backend.rpa.intent_models import IntentPlan


def route_intent(intent: IntentPlan) -> str:
    return intent.route
