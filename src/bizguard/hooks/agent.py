"""Agent hook delegates to the shared CI evaluator without trusting an agent claim."""

from bizguard.ci.check import evaluate


def validate(diff_text: str) -> dict[str, object]:
    return evaluate(diff_text)
