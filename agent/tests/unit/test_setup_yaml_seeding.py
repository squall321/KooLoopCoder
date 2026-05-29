"""setup.sh stage 0 requires the operator-authored YAMLs.

We deliberately do NOT seed defaults from the bundle's *.yaml.example.
Auto-seeding would silently set the box up for the example's model
(e.g. 480B-FP8) rather than what the operator actually intends to
serve. Stage 0 instead fails loudly with the exact commands needed.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"
CONFIG = REPO / "config"


def test_all_three_yaml_examples_are_shipped():
    for name in ("install.yaml.example", "vllm.yaml.example",
                 "loopcoder.yaml.example"):
        assert (CONFIG / name).is_file(), f"missing config/{name}"


def _stage0(body: str) -> str:
    """Return stage_0_preflight body, robust to nested heredoc braces."""
    start = body.find("stage_0_preflight() {")
    # The function ends at the first line that is exactly `}` (anchored
    # to the start of a line); heredoc-embedded braces don't satisfy that.
    end = body.index("\n}\n", start)
    return body[start:end]


def test_stage0_does_not_silently_seed_yamls():
    # The old auto-seed must be gone — we explicitly do not run `cp` on
    # the templates inside stage_0_preflight.
    stage0 = _stage0(SETUP.read_text())
    assert "cp '$tmpl'" not in stage0
    assert "seeded" not in stage0


def test_stage0_lists_each_missing_yaml_in_the_error():
    stage0 = _stage0(SETUP.read_text())
    # The error must enumerate the missing files and offer copy commands.
    assert "missing config under" in stage0
    assert "yaml.example" in stage0
    assert "mkdir -p $ETC_DIR" in stage0
    # Operator is reminded to edit install.yaml (the file that picks the model).
    assert "$EDITOR" in stage0
    assert "model.id" in stage0


def test_models_list_does_not_silently_swallow_errors():
    body = SETUP.read_text()
    block = body.split("models_list() {", 1)[1].split("}", 1)[0]
    assert "2>/dev/null || true" not in block
    assert "INSTALL_YAML" in block
    assert '[[ -f "$INSTALL_YAML" ]] || return 0' in block


def test_default_model_key_does_not_silently_swallow_errors():
    body = SETUP.read_text()
    block = body.split("default_model_key() {", 1)[1].split("}", 1)[0]
    assert "2>/dev/null || true" not in block
