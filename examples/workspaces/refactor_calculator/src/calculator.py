"""Monolithic calculator — to be refactored.

Public API: ``evaluate(expr: str) -> float``. Tests must keep passing
through every refactor step. The body of evaluate() does both lexing
and parsing inline; the plan asks the agent to split _tokenize and
_parse out of this function.
"""


def evaluate(expr: str) -> float:
    """Evaluate a simple arithmetic expression with +, -, *, / and parens.

    Operator precedence: * and / before + and -. No unary operators, no
    floating-point literals beyond a simple decimal point.
    """
    # ---- inline tokenizer ----
    tokens: list[tuple[str, str | float]] = []
    i = 0
    s = expr.replace(" ", "")
    while i < len(s):
        c = s[i]
        if c.isdigit() or c == ".":
            j = i
            while j < len(s) and (s[j].isdigit() or s[j] == "."):
                j += 1
            tokens.append(("num", float(s[i:j])))
            i = j
        elif c in "+-*/()":
            tokens.append(("op", c))
            i += 1
        else:
            raise ValueError(f"unexpected char: {c!r}")

    # ---- inline parser (shunting-yard) ----
    output: list[tuple[str, str | float]] = []
    stack: list[str] = []
    prec = {"+": 1, "-": 1, "*": 2, "/": 2}
    for tk, val in tokens:
        if tk == "num":
            output.append((tk, val))
        elif val == "(":
            stack.append("(")
        elif val == ")":
            while stack and stack[-1] != "(":
                output.append(("op", stack.pop()))
            if not stack:
                raise ValueError("mismatched parens")
            stack.pop()
        else:  # operator
            while stack and stack[-1] != "(" and prec[stack[-1]] >= prec[val]:  # type: ignore[index]
                output.append(("op", stack.pop()))
            stack.append(val)  # type: ignore[arg-type]
    while stack:
        op = stack.pop()
        if op == "(":
            raise ValueError("mismatched parens")
        output.append(("op", op))

    # ---- evaluate RPN ----
    eval_stack: list[float] = []
    for tk, val in output:
        if tk == "num":
            eval_stack.append(val)  # type: ignore[arg-type]
        else:
            b = eval_stack.pop()
            a = eval_stack.pop()
            if val == "+":
                eval_stack.append(a + b)
            elif val == "-":
                eval_stack.append(a - b)
            elif val == "*":
                eval_stack.append(a * b)
            elif val == "/":
                eval_stack.append(a / b)
    if len(eval_stack) != 1:
        raise ValueError("malformed expression")
    return eval_stack[0]
