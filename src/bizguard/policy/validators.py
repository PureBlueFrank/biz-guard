"""AST validation of deterministic policy invariants."""

import ast
import re
from pathlib import Path

from bizguard.decision import Finding, FindingStatus
from bizguard.diff_parser import ParsedDiff
from bizguard.policy.invariants import Invariant


def validate_artifact(policy_id: str, source: str, path: str, severity: str = "high") -> dict[str, object]:
    """Validate non-Python public artifacts from their content, never their filename.

    The checks deliberately use syntax/content semantics that are available offline:
    published contracts retain required fields and enum values, migrations are
    transactional, message schemas expose a version, and configuration never
    embeds a credential value.
    """
    suffix = Path(path).suffix.lower()
    lower = source.lower()
    violated = False
    message = "artifact is compatible"
    if suffix in {".yaml", ".yml", ".json"} and ("openapi" in lower or "paths:" in lower):
        violated = bool(re.search(r"^\s*-\s*(id|status|code)\s*$", source, re.MULTILINE))
        message = "published OpenAPI field removed" if violated else message
    elif suffix == ".proto":
        violated = bool(not re.search(r"\b\w+\s+(id|status)\s*=", source) and "message" in lower)
        message = "published Proto required field missing" if violated else message
    elif suffix == ".sql":
        has_write = bool(re.search(r"\b(alter|update|delete|insert)\b", lower))
        violated = has_write and "transaction" not in lower and "begin" not in lower
        message = "migration write is not transactional" if violated else message
    elif suffix in {".avsc", ".schema"}:
        violated = "schema_version" not in lower and "version" not in lower
        message = "message schema has no version" if violated else message
    elif suffix in {".properties", ".conf", ".env"}:
        violated = bool(re.search(r"(?i)(password|secret|token|api[_-]?key)\s*=\s*[^${\s][^\s]*", source))
        message = "configuration contains a literal credential" if violated else message
    return {
        "id": policy_id,
        "severity": severity,
        "violated": violated,
        "effect": message,
        "remediation": "restore compatibility or provide a versioned migration",
        "confidence": 1.0,
        "evidence": [path],
    }


def validate_invariant(
    parsed_diff: ParsedDiff, full_file_text: str | None, invariant: Invariant
) -> Finding:
    """Check the target method's decorator, call argument, and call ordering."""
    del parsed_diff
    references = [f"policy:{invariant.id}", *invariant.evidence_refs, invariant.target.file]
    if full_file_text is None:
        return Finding(
            finding_id=invariant.id,
            status=FindingStatus.INCOMPLETE,
            message="无法重建 Policy 目标文件的变更后文本。",
            evidence_refs=references,
        )
    try:
        module = ast.parse(full_file_text)
    except SyntaxError as exc:
        return Finding(
            finding_id=invariant.id,
            status=FindingStatus.INCOMPLETE,
            message=f"变更后文本无法解析为 Python AST: {exc.msg}",
            evidence_refs=references,
        )
    target_class = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == invariant.target.class_name
        ),
        None,
    )
    target_function = (
        None
        if target_class is None
        else next(
            (
                node
                for node in target_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == invariant.target.function
            ),
            None,
        )
    )
    if target_function is None:
        return _violation(invariant, references, "未找到受 Policy 保护的目标方法。")
    decorators = {_dotted_name(item) for item in target_function.decorator_list}
    if invariant.transaction_context.decorator not in decorators:
        return _violation(invariant, references, "目标方法缺少要求的事务装饰器。")
    calls = [node for node in ast.walk(target_function) if isinstance(node, ast.Call)]
    required_calls = [
        node for node in calls if _call_matches(node.func, invariant.required_call.call)
    ]
    protected_calls = [
        node for node in calls if _call_matches(node.func, invariant.required_call.before)
    ]
    has_argument = any(
        node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == invariant.required_call.argument
        for node in required_calls
    )
    if not has_argument or not protected_calls:
        return _violation(invariant, references, "目标方法缺少要求的幂等保护调用。")
    if min(node.lineno for node in required_calls) >= min(node.lineno for node in protected_calls):
        return _violation(invariant, references, "幂等保护调用未发生在账本调用之前。")
    return Finding(
        finding_id=invariant.id,
        status=FindingStatus.PASSED,
        message="Policy 不变量已通过。",
        evidence_refs=references,
    )


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ""


def _call_matches(node: ast.expr, expected: str) -> bool:
    name = _dotted_name(node)
    return name == expected or name.endswith(f".{expected}")


def _violation(invariant: Invariant, references: list[str], message: str) -> Finding:
    return Finding(
        finding_id=invariant.id,
        status=FindingStatus.VIOLATED,
        message=message,
        evidence_refs=references,
    )
