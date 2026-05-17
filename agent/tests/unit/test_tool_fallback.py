"""Tests for content-fallback tool-call recovery."""

import json

from loopcoder.llm.tool_fallback import extract_tool_calls

ALLOWED = {"write_file", "run_shell"}


def _one(content):
    return extract_tool_calls(content, ALLOWED)


def test_xml_response_wrapper():
    c = (
        '```xml\n<response>\n    {\n        "name": "write_file",\n'
        '        "arguments": {"path": "hello.py", "content": "print(1)"}\n'
        "    }\n</response>\n```"
    )
    out = _one(c)
    assert len(out) == 1
    assert out[0]["name"] == "write_file"
    assert json.loads(out[0]["arguments"]) == {"path": "hello.py", "content": "print(1)"}


def test_json_fence():
    c = '```json\n{"name": "run_shell", "arguments": {"cmd": "ls"}}\n```'
    out = _one(c)
    assert out == [{"name": "run_shell", "arguments": json.dumps({"cmd": "ls"})}]


def test_tool_call_tag():
    c = '<tool_call>{"name": "write_file", "arguments": {"path": "a", "content": "b"}}</tool_call>'
    out = _one(c)
    assert len(out) == 1 and out[0]["name"] == "write_file"


def test_bare_object():
    c = 'Sure, I will do that.\n{"name": "write_file", "arguments": {"path": "x", "content": "y"}}'
    out = _one(c)
    assert len(out) == 1 and out[0]["name"] == "write_file"


def test_parameters_alias():
    c = '{"name": "write_file", "parameters": {"path": "p", "content": "c"}}'
    out = _one(c)
    assert json.loads(out[0]["arguments"]) == {"path": "p", "content": "c"}


def test_name_not_allowed_is_ignored():
    c = '{"name": "delete_everything", "arguments": {}}'
    assert _one(c) == []


def test_plain_prose_yields_nothing():
    assert _one("I think we should create a file called hello.py.") == []


def test_none_and_empty():
    assert extract_tool_calls(None, ALLOWED) == []
    assert extract_tool_calls("", ALLOWED) == []
    assert extract_tool_calls('{"name":"write_file","arguments":{}}', set()) == []


def test_dedupe_identical_calls():
    c = (
        '```json\n{"name":"write_file","arguments":{"path":"a","content":"b"}}\n```\n'
        '{"name":"write_file","arguments":{"path":"a","content":"b"}}'
    )
    assert len(_one(c)) == 1


def test_function_name_nested():
    c = '{"function": {"name": "run_shell"}, "arguments": {"cmd": "pwd"}}'
    out = _one(c)
    assert len(out) == 1 and out[0]["name"] == "run_shell"
