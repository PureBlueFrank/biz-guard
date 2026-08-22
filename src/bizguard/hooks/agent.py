"""Agent hook delegates to the shared evaluator without trusting an agent claim."""

from pathlib import Path

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import ChangeDecision, EvaluationRequest


def validate(diff_text: str, repository_root: Path | None = None) -> ChangeDecision:
    """Evaluate a diff with the canonical change evaluator; performs no decision of its own."""
    root = repository_root or Path(__file__).parents[3] / "fixtures" / "java-microservices"
    return ChangeEvaluator(root).evaluate(
        EvaluationRequest(diff_text=diff_text, repository_root=root)
    )
