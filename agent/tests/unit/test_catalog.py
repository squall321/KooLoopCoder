"""Tests for the hardware-profile model catalog + recommender."""

from pathlib import Path

import pytest

from loopcoder.catalog import Catalog, CatalogModel, HardwareProfile, load_catalog


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
    import json, sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "b300x8", "--json"])
    rc = recommend_cli()
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["profile"] == "b300x8"
    assert isinstance(body["models"], list)
    assert len(body["models"]) >= 1


def test_select_model_list_returns_more_than_one(monkeypatch, capsys):
    import json, sys
    from loopcoder.catalog import recommend_cli

    monkeypatch.setattr(sys, "argv", ["x", "b300x8", "--list", "--json"])
    rc = recommend_cli()
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert len(body["models"]) > 1
