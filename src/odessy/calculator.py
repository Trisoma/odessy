from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import TypedDict


class CalculatorEvalResult(TypedDict):
    expression: str
    result: int | float
    status: str


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[int | float, int | float], int | float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[int | float], int | float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def evaluate_arithmetic_expression(expression: str) -> CalculatorEvalResult:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("calculator.eval requires a non-empty arithmetic expression")
    if len(expression) > 200:
        raise ValueError("calculator.eval expression is too long")

    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("calculator.eval received invalid arithmetic syntax") from exc

    return {
        "expression": expression.strip(),
        "result": _evaluate_node(parsed.body),
        "status": "ok",
    }


def _evaluate_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("calculator.eval only accepts numeric literals")
        return node.value

    if isinstance(node, ast.BinOp):
        operator_fn = _BINARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("calculator.eval only supports +, -, *, and /")
        return operator_fn(_evaluate_node(node.left), _evaluate_node(node.right))

    if isinstance(node, ast.UnaryOp):
        operator_fn = _UNARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("calculator.eval only supports unary + and -")
        return operator_fn(_evaluate_node(node.operand))

    raise ValueError("calculator.eval only accepts side-effect-free arithmetic")
