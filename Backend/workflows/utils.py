"""Utility helpers for workflow parameter resolution and conditions."""
import ast
import re
from typing import Any, Dict

_TEMPLATE_RE = re.compile(r"{{\s*([^}]+)\s*}}")


class DotDict(dict):
    """Dictionary with attribute access for safe eval."""

    def __getattr__(self, item):
        value = self.get(item)
        if isinstance(value, dict):
            return DotDict(value)
        return value


def to_dotdict(value: Any) -> Any:
    if isinstance(value, dict):
        return DotDict({k: to_dotdict(v) for k, v in value.items()})
    if isinstance(value, list):
        return [to_dotdict(v) for v in value]
    return value


def get_context_value(path: str, context: Dict[str, Any]) -> Any:
    parts = [p for p in path.split('.') if p]
    current: Any = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def resolve_template(value: Any, context: Dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value

    matches = _TEMPLATE_RE.findall(value)
    if not matches:
        return value

    if len(matches) == 1 and value.strip() == f"{{{{{matches[0]}}}}}":
        return get_context_value(matches[0].strip(), context)

    resolved = value
    for expr in matches:
        replacement = get_context_value(expr.strip(), context)
        resolved = resolved.replace(f"{{{{{expr}}}}}", str(replacement))
    return resolved


def resolve_parameters(params: Any, context: Dict[str, Any]) -> Any:
    if isinstance(params, dict):
        return {k: resolve_parameters(v, context) for k, v in params.items()}
    if isinstance(params, list):
        return [resolve_parameters(v, context) for v in params]
    return resolve_template(params, context)


_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)


def safe_eval_condition(expr: str, context: Dict[str, Any]) -> bool:
    if not expr:
        return True

    try:
        tree = ast.parse(expr, mode='eval')
    except Exception:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return False

    eval_context = {k: to_dotdict(v) for k, v in context.items()}

    try:
        return bool(eval(compile(tree, '<condition>', 'eval'), {"__builtins__": {}}, eval_context))
    except Exception:
        return False


def compact_context(
    context: Dict[str, Any],
    max_items: int = 5,
    max_chars: int = 2000,
    max_keys: int = 50,
    max_depth: int = 4,
) -> Dict[str, Any]:
    """
    Reduce workflow context size for storage/LLM summarization.
    Keeps execution-time context intact; use only after workflow completes.
    """
    def _compact(value: Any, depth: int = 0) -> Any:
        if depth >= max_depth:
            return "[truncated]"
        if isinstance(value, dict):
            compacted = {}
            for idx, (key, val) in enumerate(value.items()):
                if idx >= max_keys:
                    compacted["_truncated_keys"] = len(value) - max_keys
                    break
                compacted[key] = _compact(val, depth + 1)
            return compacted
        if isinstance(value, list):
            return [_compact(v, depth + 1) for v in value[:max_items]]
        if isinstance(value, str) and len(value) > max_chars:
            return value[:max_chars] + "...[truncated]"
        return value

    return _compact(context, 0) if isinstance(context, dict) else {}
