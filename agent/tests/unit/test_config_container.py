"""Tests for ContainerConfig: suite_image, store_dir, current_dir."""

from pathlib import Path

from loopcoder.config import ContainerConfig, load_install_config


def test_container_defaults_have_paths():
    c = ContainerConfig.model_validate({"vllm_image": "/x.sif", "sandbox_image": "/y.sif"})
    assert c.suite_image is None
    assert c.store_dir == "/opt/apptainers"
    assert c.current_dir == "/opt/apptainers/current"


def test_container_with_suite_image():
    c = ContainerConfig.model_validate({
        "vllm_image": "/opt/apptainers/current/vllm.sif",
        "sandbox_image": "/opt/apptainers/current/loopcoder-sandbox.sif",
        "suite_image": "/opt/apptainers/current/loopcoder-suite.sif",
    })
    assert c.suite_image.endswith("loopcoder-suite.sif")


def test_install_yaml_example_loads(tmp_path: Path):
    """The shipped example must parse cleanly through the new schema."""
    src = Path(__file__).resolve().parents[3] / "config" / "install.yaml.example"
    cfg = load_install_config(src)
    assert cfg.container.store_dir == "/opt/apptainers"
    assert cfg.container.current_dir == "/opt/apptainers/current"
    assert cfg.container.suite_image is not None
    assert "loopcoder-suite" in cfg.container.suite_image


def test_install_yaml_tiny_loads():
    src = Path(__file__).resolve().parents[3] / "config" / "install.yaml.tiny"
    cfg = load_install_config(src)
    assert cfg.container.suite_image is not None
    assert cfg.model.id == "Qwen/Qwen2.5-Coder-0.5B-Instruct"


def test_custom_store_dir():
    c = ContainerConfig.model_validate({
        "vllm_image": "/x.sif",
        "sandbox_image": "/y.sif",
        "store_dir": "/var/lib/containers",
        "current_dir": "/var/lib/containers/active",
    })
    assert c.store_dir == "/var/lib/containers"
    assert c.current_dir == "/var/lib/containers/active"
