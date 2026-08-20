"""Agent hook delegates to the shared CI evaluator without trusting an agent claim."""

from bizguard.ci.check import evaluate


def validate(diff_text: str) -> dict[str, object]:
    """Evaluate a diff with the shared CI policy checks."""
    return evaluate(diff_text)
