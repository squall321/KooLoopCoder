"""setup.sh stage 0 auto-seeds /etc/loopcoder/*.yaml on first install.

Before this, an operator running `sudo bash setup.sh` on a fresh B300
would survive through stage 3-8 and then fail at stage 7 or stage 9
when `install.yaml` was missing from /etc/loopcoder/. The Mode A/B
deploy paths never copied the YAMLs either, so "ready for B300" was
quietly broken.

These tests pin the contract: stage 0 must seed the three YAMLs from
the bundle's config/*.yaml.example when they're absent, and it must
not silently drop parse errors when the file IS present.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"
CONFIG = REPO / "config"


def test_all_three_yaml_examples_are_shipped():
    # If any of these stop being shipped, the seed step below cannot run.
    for name in ("install.yaml.example", "vllm.yaml.example",
                 "loopcoder.yaml.example"):
        assert (CONFIG / name).is_file(), f"missing config/{name}"


def test_stage0_seeds_missing_yamls_from_bundle_examples():
    body = SETUP.read_text()
    # Stage 0 has explicit "config staging" logic.
    assert "config templates not found" in body
    assert "yaml.example" in body
    # It iterates install / vllm / loopcoder.
    for name in ("install", "vllm", "loopcoder"):
        assert f"\"{name}\"" in body or f"'{name}'" in body or name in body


def test_stage0_fails_loudly_if_seed_still_missing():
    body = SETUP.read_text()
    assert 'still missing $INSTALL_YAML after seeding' in body


def test_models_list_does_not_silently_swallow_errors():
    body = SETUP.read_text()
    # We intentionally drop the `2>/dev/null || true` from models_list /
    # default_model_key — a parse error in install.yaml must reach the
    # operator instead of producing an empty list that triggers the
    # single-model fallback.
    block = body.split("models_list() {", 1)[1].split("}", 1)[0]
    assert "2>/dev/null || true" not in block
    assert "INSTALL_YAML" in block

    block2 = body.split("default_model_key() {", 1)[1].split("}", 1)[0]
    assert "2>/dev/null || true" not in block2


def test_models_list_short_circuits_when_install_yaml_absent():
    body = SETUP.read_text()
    # Without install.yaml on the host (e.g. stage 0 not yet run in a
    # weird invocation), we must return 0 before touching apptainer.
    block = body.split("models_list() {", 1)[1].split("}", 1)[0]
    assert '[[ -f "$INSTALL_YAML" ]] || return 0' in block


def test_stage0_seed_message_tells_operator_to_review():
    body = SETUP.read_text()
    assert "review" in body and "before going to production" in body
