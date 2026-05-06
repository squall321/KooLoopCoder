import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.calculator import evaluate


def test_simple_add():
    assert evaluate("1 + 2") == 3


def test_precedence():
    assert evaluate("1 + 2 * 3") == 7


def test_parens():
    assert evaluate("(1 + 2) * 3") == 9


def test_subtract_divide():
    assert evaluate("10 - 4 / 2") == 8


def test_decimals():
    assert evaluate("1.5 * 2") == 3.0


def test_nested():
    assert evaluate("((2+3)*4) - 1") == 19
