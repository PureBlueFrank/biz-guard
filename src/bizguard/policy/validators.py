"""Deterministic structural validation of governed policy artifacts."""

import ast
import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.decision import Finding, FindingStatus
from bizguard.diff_parser import ParsedDiff
from bizguard.policy.invariants import Invariant


def validate_artifact(
    policy_id: str,
    source: str,
    path: str,
    severity: str = "high",
    *,
    baseline_source: str | None = None,
) -> dict[str, object]:
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
    baseline_lower = baseline_source.lower() if baseline_source is not None else ""
    api_markers = ("openapi", "swagger", "paths:", "components:", "definitions:")
    if suffix in {".yaml", ".yml", ".json"} and any(
        marker in lower or marker in baseline_lower for marker in api_markers
    ):
        violated = _openapi_has_missing_required_field(source) or (
            baseline_source is not None
            and _openapi_removed_fields(baseline_source, source)
        )
        message = "published OpenAPI field removed" if violated else message
    elif suffix == ".proto":
        current_fields = _proto_fields(source)
        if baseline_source is not None:
            baseline_fields = _proto_fields(baseline_source)
            violated = any(current_fields.get(key) != number for key, number in baseline_fields.items())
        else:
            violated = bool("message" in lower and not any(name in {"id", "status"} for _, name in current_fields))
        message = "published Proto required field missing" if violated else message
    elif suffix == ".sql":
        violated = _sql_has_untransactional_write(source)
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
        "precision": "high" if suffix in {".yaml", ".yml", ".json"} else "medium",
        "evidence": [path],
    }


def _proto_fields(source: str) -> dict[tuple[str, str], int]:
    """Extract fields from nested messages and oneofs with balanced-brace parsing."""
    token_pattern = re.compile(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|'
        r"[A-Za-z_]\w*|\d+|[{};=<>\[\],.]",
        flags=re.DOTALL,
    )
    tokens = [
        token
        for token in token_pattern.findall(source)
        if not token.startswith(("//", "/*", '"', "'"))
    ]
    fields: dict[tuple[str, str], int] = {}

    def record(statement: list[str], message_name: str | None) -> None:
        if message_name is None or "=" not in statement:
            return
        equals = statement.index("=")
        identifiers = [token for token in statement[:equals] if re.fullmatch(r"\w+", token)]
        if len(identifiers) < 2 or identifiers[0] in {
            "option",
            "reserved",
            "extensions",
            "rpc",
        }:
            return
        number = next(
            (int(token) for token in statement[equals + 1 :] if token.isdigit()),
            None,
        )
        if number is not None:
            fields[(message_name, identifiers[-1])] = number

    def parse_scope(position: int, message_name: str | None) -> int:
        statement: list[str] = []
        while position < len(tokens):
            token = tokens[position]
            if token == "}":
                record(statement, message_name)
                return position + 1
            if token == "message" and position + 1 < len(tokens):
                nested = tokens[position + 1]
                brace = position + 2
                while brace < len(tokens) and tokens[brace] != "{":
                    brace += 1
                if brace == len(tokens):
                    return len(tokens)
                qualified = f"{message_name}.{nested}" if message_name else nested
                position = parse_scope(brace + 1, qualified)
                statement = []
                continue
            if token == "oneof":
                brace = position + 1
                while brace < len(tokens) and tokens[brace] != "{":
                    brace += 1
                if brace == len(tokens):
                    return len(tokens)
                position = parse_scope(brace + 1, message_name)
                statement = []
                continue
            if token == "{":
                position = parse_scope(position + 1, message_name)
                statement = []
                continue
            if token == ";":
                record(statement, message_name)
                statement = []
            else:
                statement.append(token)
            position += 1
        record(statement, message_name)
        return position

    parse_scope(0, None)
    return fields


def _openapi_removed_fields(before: str, after: str) -> bool:
    """Compare named schema properties across two OpenAPI documents."""
    try:
        old_document = yaml.safe_load(before)
        new_document = yaml.safe_load(after)
    except yaml.YAMLError:
        return True
    old = _openapi_schema_properties(old_document)
    new = _openapi_schema_properties(new_document)
    return any(not fields.issubset(new.get(schema, set())) for schema, fields in old.items())


def _openapi_schema_properties(document: object) -> dict[str, set[tuple[str, ...]]]:
    """Collect property paths from named and inline OpenAPI/Swagger schemas."""
    if not isinstance(document, dict):
        return {}
    collected: dict[str, set[tuple[str, ...]]] = {}

    def schema_fields(schema: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
        if not isinstance(schema, dict):
            return set()
        result: set[tuple[str, ...]] = set()
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                field_path = (*prefix, str(name))
                result.add(field_path)
                result.update(schema_fields(child, field_path))
        for composition in ("allOf", "oneOf", "anyOf"):
            branches = schema.get(composition)
            if isinstance(branches, list):
                for branch in branches:
                    result.update(schema_fields(branch, prefix))
        if "items" in schema:
            result.update(schema_fields(schema["items"], (*prefix, "[]")))
        if "additionalProperties" in schema:
            result.update(
                schema_fields(schema["additionalProperties"], (*prefix, "{}"))
            )
        return result

    def add_named(container: object, prefix: str) -> None:
        if not isinstance(container, dict):
            return
        for name, schema in container.items():
            collected[f"{prefix}/{name}"] = schema_fields(schema)

    components = document.get("components")
    if isinstance(components, dict):
        add_named(components.get("schemas"), "components/schemas")
    add_named(document.get("definitions"), "definitions")

    def collect_inline(node: object, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child_path = (*path, str(key))
                if key == "schema" and isinstance(value, dict):
                    collected["/".join(child_path)] = schema_fields(value)
                collect_inline(value, child_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                collect_inline(value, (*path, str(index)))

    collect_inline(document.get("paths"), ("paths",))
    return collected


def _sql_has_untransactional_write(source: str) -> bool:
    """Check top-level SQL write tokens, including writes following CTE clauses."""
    in_transaction = False
    writes = {"alter", "create", "drop", "truncate", "update", "delete", "insert", "merge"}
    for tokens in _sql_top_level_statements(source):
        if not tokens:
            continue
        if tokens[0] == "begin" or tokens[:2] == ["start", "transaction"]:
            in_transaction = True
            continue
        if tokens[0] in {"commit", "rollback"}:
            in_transaction = False
            continue
        if writes.intersection(tokens) and not in_transaction:
            return True
    return False


def _sql_top_level_statements(source: str) -> list[list[str]]:
    """Tokenize SQL outside strings/comments and ignore words nested in parentheses."""
    statements: list[list[str]] = [[]]
    token: list[str] = []
    depth = 0
    position = 0
    quote: str | None = None
    line_comment = False
    block_comment = False

    def flush() -> None:
        if token and depth == 0:
            statements[-1].append("".join(token).lower())
        token.clear()

    while position < len(source):
        char = source[position]
        following = source[position + 1] if position + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            position += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                position += 2
            else:
                position += 1
            continue
        if quote is not None:
            if char == quote:
                if following == quote:
                    position += 2
                    continue
                quote = None
            position += 1
            continue
        if char == "-" and following == "-":
            flush()
            line_comment = True
            position += 2
            continue
        if char == "/" and following == "*":
            flush()
            block_comment = True
            position += 2
            continue
        if char in {"'", '"'}:
            flush()
            quote = char
        elif char == "(":
            flush()
            depth += 1
        elif char == ")":
            flush()
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            flush()
            statements.append([])
        elif char.isalnum() or char in {"_", "$"}:
            token.append(char)
        else:
            flush()
        position += 1
    flush()
    return [statement for statement in statements if statement]


def _openapi_has_missing_required_field(source: str) -> bool:
    """Detect a schema whose required list references a missing property."""
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError:
        return False

    def visit(value: object) -> bool:
        if isinstance(value, dict):
            required = value.get("required")
            properties = value.get("properties")
            if isinstance(required, list) and isinstance(properties, dict):
                property_names = {str(name) for name in properties}
                if any(isinstance(name, str) and name not in property_names for name in required):
                    return True
            return any(visit(item) for item in value.values())
        if isinstance(value, list):
            return any(visit(item) for item in value)
        return False

    return visit(document)


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
