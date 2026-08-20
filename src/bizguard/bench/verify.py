"""Load-only verifier for the Phase 1 versioned golden benchmark."""

import argparse
import json
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote, urlsplit

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bizguard.domain.enums import CheckStatus, DecisionState, PolicyMode
from bizguard.domain.models import ChangeContext, Decision, ImpactPath, RequiredTest
from bizguard.graph.ids import api_id, db_id, mq_id, proto_id


class ManifestTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    input_diff: str = Field(min_length=1)
    visible_knowledge_ids: list[str]
    context_golden: str
    impact_golden: str
    decision_golden: str
    expected_tests: list[RequiredTest]
    review_sources: list[str] = Field(min_length=1)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    suite: str
    tasks: list[ManifestTask]


def verify(manifest_path: Path, suite: str) -> int:
    """Validate only Phase 1-owned fields: manifest and golden contract loading."""
    try:
        if suite == "phase2":
            return _verify_phase2(manifest_path)
        manifest_path = manifest_path.resolve()
        manifest = Manifest.model_validate(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
        if manifest.suite != suite:
            raise ValueError(f"manifest suite is {manifest.suite!r}, not {suite!r}")
        if suite == "phase1" and len(manifest.tasks) != 12:
            raise ValueError("phase1 requires exactly 12 golden tasks")
        bench_root = manifest_path.parent.parent
        root = bench_root.parent
        input_diffs = [task.input_diff for task in manifest.tasks]
        if len(input_diffs) != len(set(input_diffs)):
            raise ValueError("each task must have a unique input_diff")
        for task in manifest.tasks:
            input_diff = root / task.input_diff
            if not input_diff.is_file():
                raise ValueError(f"task {task.id} input_diff does not exist: {task.input_diff}")
            context = _load_json(bench_root / task.context_golden, ChangeContext)
            impact = _load_json(bench_root / task.impact_golden, ImpactPath)
            decision = _load_json(bench_root / task.decision_golden, Decision)
            _verify_task(root, task, context, impact, decision)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"verification failed: {exc}")
        return 1
    print(f"{len(manifest.tasks)}/{len(manifest.tasks)} {suite} manifest/schema/golden references loaded")
    return 0


def _verify_phase2(manifest_path: Path) -> int:
    """Verify six frozen semantic mappings without mutating the Phase 1 manifest."""
    root = manifest_path.resolve().parent.parent.parent
    from bizguard.semantic.models import load_catalog
    from bizguard.semantic.required_tests import select_required_tests

    payload = yaml.safe_load((root / "bench/golden/semantic/phase2.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list) or len(payload["tasks"]) != 6:
        raise ValueError("phase2 requires exactly six semantic tasks")
    catalog = load_catalog(root / "src/bizguard/semantic/catalog.yaml")
    for task in payload["tasks"]:
        capability = catalog.capability(task["capability"])
        if capability.owner != task["owner"]:
            raise ValueError(f"semantic owner mismatch: {task['id']}")
        selected = select_required_tests(catalog, task["capability"], task["mandatory_policy"])
        ids = [item.id for item in selected]
        if ids != [task["required_test"]] or task["bait_test"] in ids:
            raise ValueError(f"semantic required-test mismatch: {task['id']}")
    print("6/6 phase2 semantic tasks verified")
    return 0


ModelT = TypeVar("ModelT", ChangeContext, ImpactPath, Decision)


def _load_json(path: Path, schema: type[ModelT]) -> ModelT:
    """Load a golden object through its owned Pydantic schema."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    return schema.model_validate(payload)


def _verify_task(
    root: Path, task: ManifestTask, context: ChangeContext, impact: ImpactPath, decision: Decision
) -> None:
    expected_diff_uri = f"repo://bizguard/{task.input_diff}"
    if context.diff_uri != expected_diff_uri:
        raise ValueError(f"task {task.id} diff_uri must equal {expected_diff_uri}")
    if context.visible_knowledge_ids != task.visible_knowledge_ids:
        raise ValueError(f"task {task.id} visible knowledge IDs disagree with its context golden")
    if task.expected_tests != decision.required_tests:
        raise ValueError(f"task {task.id} expected tests disagree with its decision golden")
    if any(left == right for left, right in zip(impact.node_ids, impact.node_ids[1:])):
        raise ValueError(f"task {task.id} impact path contains a self-loop")
    _verify_outcome(task.id, decision)
    uris = [
        *task.review_sources,
        *(artifact.uri for artifact in context.artifacts),
        *impact.node_ids,
        *(_evidence_uris(context)),
        *(_evidence_uris(impact)),
        *(_evidence_uris(decision)),
    ]
    for uri in uris:
        _verify_canonical_id(uri)
        if uri.startswith("repo://"):
            _resolve_repo_uri(root, uri)


def _verify_outcome(task_id: str, decision: Decision) -> None:
    if decision.outcome is DecisionState.ALLOW and any(
        finding.status in {CheckStatus.VIOLATED, CheckStatus.INCOMPLETE} for finding in decision.findings
    ):
        raise ValueError(f"task {task_id} ALLOW decision contains a failed or incomplete finding")
    if decision.outcome is DecisionState.REQUIRE_APPROVAL and not any(
        finding.unknown_reason is not None for finding in decision.findings
    ):
        raise ValueError(f"task {task_id} REQUIRE_APPROVAL decision lacks an unknown reason")
    if any(
        finding.status is CheckStatus.VIOLATED and finding.policy_mode is PolicyMode.BLOCKING
        for finding in decision.findings
    ) and decision.outcome is not DecisionState.BLOCK:
        raise ValueError(f"task {task_id} has a blocking violation but is not BLOCK")


def _evidence_uris(model: ChangeContext | ImpactPath | Decision) -> list[str]:
    uris = [item.evidence_uri for item in model.evidence]
    if isinstance(model, ChangeContext):
        for artifact in model.artifacts:
            uris.extend(item.evidence_uri for item in artifact.evidence)
    if isinstance(model, Decision):
        for finding in model.findings:
            uris.extend(item.evidence_uri for item in finding.evidence)
        for required_test in model.required_tests:
            uris.extend(item.evidence_uri for item in required_test.evidence)
    return uris


def _verify_canonical_id(uri: str) -> None:
    """Reject IDs that do not round-trip through the frozen Phase 0 factories."""
    if not uri.startswith(("api://", "proto://", "db://", "mq://")):
        return
    parsed = urlsplit(uri)
    parts = [unquote(part) for part in parsed.path.lstrip("/").split("/") if part]
    fragment = unquote(parsed.fragment) or None
    if parsed.scheme == "api" and len(parts) >= 2:
        expected = api_id(unquote(parsed.netloc), parts[0], "/" + "/".join(parts[1:]), fragment)
    elif parsed.scheme == "proto" and len(parts) == 2:
        expected = proto_id(unquote(parsed.netloc), parts[0], parts[1])
    elif parsed.scheme == "db" and len(parts) == 1:
        expected = db_id(unquote(parsed.netloc), parts[0], fragment)
    elif parsed.scheme == "mq" and len(parts) == 1:
        expected = mq_id(unquote(parsed.netloc), parts[0], fragment)
    else:
        raise ValueError(f"invalid canonical ID shape: {uri}")
    if uri != expected:
        raise ValueError(f"canonical ID does not match its factory: {uri}")


def _resolve_repo_uri(root: Path, uri: str) -> Path:
    parsed = urlsplit(uri)
    repository = unquote(parsed.netloc)
    relative_path = Path(unquote(parsed.path.lstrip("/")))
    if not repository or not relative_path.parts or ".." in relative_path.parts:
        raise ValueError(f"invalid repo evidence URI: {uri}")
    repository_root = root if repository == "bizguard" else root / "fixtures" / "java-microservices" / repository
    target = repository_root / relative_path
    if not target.is_file():
        raise ValueError(f"repo evidence URI does not resolve to a file: {uri}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify BizGuard golden benchmark contracts.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--offline", action="store_true", help="Accepted for deterministic Phase 2 verification.")
    args = parser.parse_args()
    raise SystemExit(verify(args.manifest, args.suite))


if __name__ == "__main__":
    main()
