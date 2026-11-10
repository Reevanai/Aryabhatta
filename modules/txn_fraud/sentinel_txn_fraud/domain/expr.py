"""Rule expressions: a small, typed, side-effect-free language stored as JSON.

A rule is data, never code. An expression is one of:

- a literal: number, string, boolean or null
- `{"field": name}`    a property of the transaction being scored
- `{"feature": name}`  a computed feature (velocity, history, device, ...)
- `{"param": name}`    a tenant threshold from the active threshold profile
- `{"op": name, "args": [...]}`

Comparisons involving a missing value (null) are false; arithmetic on a missing value is
missing. Evaluation records every value it read, which becomes the explanation of a hit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

Type = str  # "number" | "string" | "bool" | "null"
BOOL_OPS = {"and", "or", "not"}
COMPARE_OPS = {"eq", "ne", "gt", "gte", "lt", "lte"}
ARITH_OPS = {"add", "sub", "mul", "div", "min", "max"}
OPS = BOOL_OPS | COMPARE_OPS | ARITH_OPS | {"between", "in"}
MAX_DEPTH = 12
MAX_NODES = 200


@dataclass(frozen=True)
class Vocabulary:
    """Names an expression may refer to, with their types."""

    fields: Mapping[str, Type]
    features: Mapping[str, Type]
    params: Mapping[str, Type]

    def lookup(self, kind: str, name: str) -> Type | None:
        table: Mapping[str, Type] = getattr(self, kind + "s")
        return table.get(name)


class InvalidExpression(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _literal_type(value: Any) -> Type | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def validate(expr: Any, vocabulary: Vocabulary) -> None:
    """Check structure, names and types. A valid rule is a boolean expression."""
    errors: list[str] = []
    count = [0]

    def walk(node: Any, path: str, depth: int) -> Type | None:
        count[0] += 1
        if depth > MAX_DEPTH:
            errors.append(f"{path}: expression is nested too deeply")
            return None
        literal = _literal_type(node)
        if literal is not None:
            return literal
        if not isinstance(node, dict):
            errors.append(f"{path}: expected a value, a reference or an operation")
            return None
        for kind in ("field", "feature", "param"):
            if kind in node:
                if set(node) != {kind} or not isinstance(node[kind], str):
                    errors.append(f"{path}: a {kind} reference takes only a name")
                    return None
                found = vocabulary.lookup(kind, node[kind])
                if found is None:
                    errors.append(f"{path}: unknown {kind} '{node[kind]}'")
                return found
        if set(node) != {"op", "args"} or not isinstance(node["args"], list):
            errors.append(f"{path}: an operation needs exactly 'op' and 'args'")
            return None
        op, args = node["op"], node["args"]
        if op not in OPS:
            errors.append(f"{path}: unknown operation '{op}'")
            return None
        if op == "in":
            if len(args) != 2 or not isinstance(args[1], list) or not args[1]:
                errors.append(f"{path}: 'in' takes a value and a non-empty list of literals")
                return "bool"
            left = walk(args[0], f"{path}.args[0]", depth + 1)
            for item in args[1]:
                item_type = _literal_type(item)
                if item_type is None or (left and item_type != left):
                    errors.append(f"{path}: list items must be {left or 'literal'} values")
                    break
            return "bool"
        types = [walk(arg, f"{path}.args[{i}]", depth + 1) for i, arg in enumerate(args)]
        known = [t for t in types if t not in (None, "null")]

        def need(n_min: int, n_max: int | None, wanted: Type | None) -> None:
            if len(args) < n_min or (n_max is not None and len(args) > n_max):
                span = str(n_min) if n_min == n_max else f"at least {n_min}"
                errors.append(f"{path}: '{op}' takes {span} argument(s)")
            if wanted and any(t != wanted for t in known):
                errors.append(f"{path}: '{op}' needs {wanted} arguments")

        if op in {"and", "or"}:
            need(2, None, "bool")
            return "bool"
        if op == "not":
            need(1, 1, "bool")
            return "bool"
        if op in {"eq", "ne"}:
            need(2, 2, None)
            if len(set(known)) > 1:
                errors.append(f"{path}: '{op}' compares values of the same type")
            return "bool"
        if op in COMPARE_OPS:
            need(2, 2, "number")
            return "bool"
        if op == "between":
            need(3, 3, "number")
            return "bool"
        need(2, None if op in {"add", "mul", "min", "max"} else 2, "number")
        return "number"

    result = walk(expr, "rule", 0)
    if count[0] > MAX_NODES:
        errors.append("rule: expression is too large")
    if not errors and result != "bool":
        errors.append("rule: the expression must produce true or false")
    if errors:
        raise InvalidExpression(errors)


@dataclass
class Evaluation:
    hit: bool
    observed: dict[str, Any] = field(default_factory=dict)


def evaluate(
    expr: Any, fields: Mapping[str, Any], features: Mapping[str, Any], params: Mapping[str, Any]
) -> Evaluation:
    """Evaluate a validated expression. `observed` holds every value the rule read."""
    observed: dict[str, Any] = {}
    sources = {"field": fields, "feature": features, "param": params}

    def run(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        for kind, source in sources.items():
            if kind in node:
                value = source.get(node[kind])
                observed[node[kind]] = value
                return value
        op, args = node["op"], node["args"]
        if op == "and":
            return all(run(arg) is True for arg in args)
        if op == "or":
            # Evaluate every branch so the explanation shows all the values considered.
            return any([run(arg) is True for arg in args])  # noqa: C419
        if op == "not":
            return run(args[0]) is not True
        if op == "in":
            return run(args[0]) in args[1]
        values = [run(arg) for arg in args]
        if op == "eq":
            return values[0] == values[1]
        if op == "ne":
            return values[0] != values[1]
        if any(v is None for v in values):
            return None if op in ARITH_OPS else False
        if op == "gt":
            return values[0] > values[1]
        if op == "gte":
            return values[0] >= values[1]
        if op == "lt":
            return values[0] < values[1]
        if op == "lte":
            return values[0] <= values[1]
        if op == "between":
            return values[1] <= values[0] <= values[2]
        if op == "add":
            return sum(values)
        if op == "sub":
            return values[0] - values[1]
        if op == "mul":
            product = 1.0
            for v in values:
                product *= v
            return product
        if op == "div":
            return values[0] / values[1] if values[1] else None
        if op == "min":
            return min(values)
        return max(values)

    return Evaluation(run(expr) is True, observed)
