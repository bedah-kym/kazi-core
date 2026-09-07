"""Utility helpers for workflow parameter resolution and conditions."""
import ast
import operator
import re
from typing import Any, Dict

_TEMPLATE_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")


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

    # A whole-string single template returns the raw context value (so a
    # number/dict/list flows through un-stringified); anything else is a
    # string interpolation of one or more templates.
    stripped = value.strip()
    whole = _TEMPLATE_RE.fullmatch(stripped)
    if whole is not None:
        return get_context_value(whole.group(1).strip(), context)

    def _replace(match: re.Match) -> str:
        resolved = get_context_value(match.group(1).strip(), context)
        return "" if resolved is None else str(resolved)

    return _TEMPLATE_RE.sub(_replace, value)


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
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
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

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


def _eval_node(node: ast.AST, env: Dict[str, Any]) -> Any:
    # Interpret a whitelisted AST against the context dict. Attribute access is
    # restricted to plain dict entries (never dunder/object attributes), and
    # nothing in the environment is callable — so no property getter or
    # sandbox-escape chain can execute.
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.List):
        return [_eval_node(e, env) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, env) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, env): _eval_node(v, env)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        try:
            return _BINOPS[type(node.op)](left, right)
        except (TypeError, ZeroDivisionError, ValueError):
            return None
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, env) for v in node.values)
        return any(_eval_node(v, env) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, env)
            try:
                if not _CMPOPS[type(op)](left, right):
                    return False
            except (TypeError, ValueError):
                return False
            left = right
        return True
    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, env)
        if isinstance(base, dict) and not node.attr.startswith('_'):
            return base.get(node.attr)
        return None
    if isinstance(node, ast.Subscript):
        base = _eval_node(node.value, env)
        key = _eval_node(node.slice, env)
        if isinstance(base, dict):
            return base.get(key)
        if isinstance(base, (list, tuple)) and isinstance(key, int):
            try:
                return base[key]
            except (IndexError, TypeError):
                return None
        return None
    return None


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
        return bool(_eval_node(tree.body, eval_context))
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
