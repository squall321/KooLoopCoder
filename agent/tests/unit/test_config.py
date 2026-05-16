"""Tests for config loading + YAML merge."""


import pytest

from loopcoder.config import (
    LoopCoderConfig,
    VllmConfig,
    deep_merge,
    expand_env_vars,
    load_loopcoder_config,
    load_install_config,
    load_vllm_config,
    load_yaml,
)


def test_deep_merge_simple():
    base = {"a": 1, "b": {"x": 10, "y": 20}}
    override = {"b": {"y": 99, "z": 7}, "c": 3}
    result = deep_merge(base, override)
    assert result == {"a": 1, "b": {"x": 10, "y": 99, "z": 7}, "c": 3}


def test_deep_merge_lists_replace():
    # By design we REPLACE lists, not concatenate
    base = {"l": [1, 2, 3]}
    override = {"l": [9]}
    assert deep_merge(base, override) == {"l": [9]}


def test_expand_env_vars(monkeypatch):
    monkeypatch.setenv("FOO_TEST", "hello")
    assert expand_env_vars("${FOO_TEST}/world") == "hello/world"
    assert expand_env_vars(["${FOO_TEST}", 1]) == ["hello", 1]
    assert expand_env_vars({"a": "${FOO_TEST}"}) == {"a": "hello"}
    # Missing var stays as-is
    assert expand_env_vars("${THIS_DEFINITELY_NOT_SET_X}") == "${THIS_DEFINITELY_NOT_SET_X}"


def test_load_yaml_missing_returns_empty(tmp_path):
    missing = tmp_path / "no.yaml"
    assert load_yaml(missing) == {}


def test_load_yaml_top_level_must_be_mapping(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- 1\n- 2\n")
    with pytest.raises(ValueError):
        load_yaml(bad)


def test_loopcoder_config_defaults():
    cfg = load_loopcoder_config(path="/nonexistent/path/loopcoder.yaml")
    assert isinstance(cfg, LoopCoderConfig)
    assert cfg.llm.model.startswith("Qwen")
    assert cfg.context.total_budget_tokens > cfg.context.reserve_for_completion


def test_loopcoder_config_overrides(tmp_path):
    p = tmp_path / "loopcoder.yaml"
    p.write_text(
        "llm:\n  temperature: 0.7\n  base_url: http://localhost:9999/v1\n"
        "loop:\n  max_iterations_per_goal: 10\n"
    )
    cfg = load_loopcoder_config(p)
    assert cfg.llm.temperature == 0.7
    assert cfg.llm.base_url == "http://localhost:9999/v1"
    assert cfg.loop.max_iterations_per_goal == 10


def test_loopcoder_config_invalid_temp(tmp_path):
    p = tmp_path / "loopcoder.yaml"
    p.write_text("llm:\n  temperature: 9.0\n")
    with pytest.raises(Exception):
        load_loopcoder_config(p)


def test_install_config_required(tmp_path):
    p = tmp_path / "install.yaml"
    p.write_text(
        "model:\n  id: test\n  source_path: /a\n  destination_path: /b\n"
        "container:\n  vllm_image: /c.sif\n  sandbox_image: /d.sif\n"
    )
    cfg = load_install_config(p)
    assert cfg.model.id == "test"
    assert cfg.deployment.mode == "offline"


def test_install_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_install_config(tmp_path / "no.yaml")


def test_vllm_config_defaults():
    cfg = load_vllm_config("/nonexistent.yaml")
    assert isinstance(cfg, VllmConfig)
    assert cfg.engine.tensor_parallel_size == 8
    assert cfg.serving.port == 8000


def test_loopcoder_config_overrides_via_dict(tmp_path):
    p = tmp_path / "loopcoder.yaml"
    p.write_text("llm:\n  temperature: 0.4\n")
    cfg = load_loopcoder_config(p, overrides={"llm": {"temperature": 0.1}})
    assert cfg.llm.temperature == 0.1
