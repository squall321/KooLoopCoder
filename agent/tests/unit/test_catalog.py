"""Tests for the hardware-profile model catalog + recommender."""

from pathlib import Path

import pytest

from loopcoder.catalog import Catalog, load_catalog


def test_catalog_loads():
    cat = load_catalog()
    assert isinstance(cat, Catalog)
    assert "b300x8" in cat.hardware_profiles
    assert "rtx_5070ti" in cat.hardware_profiles
    assert len(cat.models) >= 5


def test_b300x8_best_is_curated_or_largest():
    cat = load_catalog()
    best = cat.best_for("b300x8")
    # Either the explicit recommendation or whatever the largest-fitting is.
    rec_id = cat.recommendations.get("b300x8")
    if rec_id:
        assert best.id == rec_id
    else:
        # Then it must be the heaviest fitting model
        fitting = cat.fitting_models("b300x8")
        assert best.approx_vram_gb == max(m.approx_vram_gb for m in fitting)


def test_rtx_5070ti_only_tiny_fits():
    cat = load_catalog()
    fitting = cat.fitting_models("rtx_5070ti")
    # Practical budget is ~13 GiB; only the 0.5B + 1.5B coder fit.
    assert any("0.5B" in m.id for m in fitting)
    assert any("1.5B" in m.id for m in fitting)
    # No 32B or 480B fits a 13 GiB budget.
    assert not any(m.approx_vram_gb > 13 for m in fitting)


def test_fits_method():
    cat = load_catalog()
    p = cat.profile("b300x8")
    qwen480 = cat.model("Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8")
    assert cat.fits(qwen480, p)
    # 1T MoE — still under 1900 GiB budget
    if any(m.params_b >= 1000 for m in cat.models):
        big = next(m for m in cat.models if m.params_b >= 1000)
        assert cat.fits(big, p)


def test_unknown_profile_raises():
    cat = load_catalog()
    with pytest.raises(KeyError):
        cat.profile("not_a_real_box")


def test_unknown_model_raises():
    cat = load_catalog()
    with pytest.raises(KeyError):
        cat.model("nobody/no-such-model")


def test_select_model_cli_smoke(tmp_path: Path, capsys, monkeypatch):
    import sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "b300x8"])
    rc = recommend_cli()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Qwen3-Coder-480B" in out


def test_select_model_unknown_profile_exits_nonzero(monkeypatch, capsys):
    import sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "nonsense"])
    rc = recommend_cli()
    assert rc != 0


def test_select_model_json_mode(monkeypatch, capsys):
    import json
    import sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "b300x8", "--json"])
    rc = recommend_cli()
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["profile"] == "b300x8"
    assert isinstance(body["models"], list)
    assert len(body["models"]) >= 1


def test_select_model_list_returns_more_than_one(monkeypatch, capsys):
    import json
    import sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "b300x8", "--list", "--json"])
    rc = recommend_cli()
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert len(body["models"]) > 1


def test_resolve_model_known_catalog_entry():
    from loopcoder.catalog import resolve_model

    info = resolve_model("Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8")
    assert info["known_in_catalog"] is True
    assert info["quantization"] == "fp8"
    assert info["tensor_parallel_size"] == 8
    assert info["max_model_len"] == 262144
    assert info["tool_call_parser"] == "hermes"
    assert info["leaf"] == "Qwen3-Coder-480B-A35B-Instruct-FP8"


def test_resolve_model_awq_maps_to_awq_marlin():
    from loopcoder.catalog import resolve_model

    info = resolve_model("Qwen/Qwen2.5-Coder-7B-Instruct-AWQ")
    assert info["quantization"] == "awq_marlin"


def test_resolve_model_unknown_uses_heuristics():
    from loopcoder.catalog import resolve_model

    info = resolve_model("some-org/Mystery-Model-13B-GPTQ")
    assert info["known_in_catalog"] is False
    assert info["quantization"] == "gptq_marlin"
    assert info["tool_call_parser"] == "hermes"


def test_resolve_model_bf16_yields_no_quantization():
    from loopcoder.catalog import resolve_model

    info = resolve_model("Qwen/Qwen2.5-Coder-1.5B-Instruct")
    assert info["quantization"] == ""  # bf16 -> don't pass --quantization


def test_resolve_cli_key_value_output(monkeypatch, capsys):
    import sys
    from loopcoder.catalog import resolve_cli

    monkeypatch.setattr(sys, "argv", ["x", "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"])
    rc = resolve_cli()
    assert rc == 0
    out = capsys.readouterr().out
    assert "MODEL_QUANTIZATION=fp8" in out
    assert "MODEL_TOOL_PARSER=hermes" in out
    assert "MODEL_TP=8" in out
