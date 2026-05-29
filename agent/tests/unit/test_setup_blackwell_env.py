"""setup.sh Blackwell env auto-injection + vllm.def flashinfer purge.

These hit-path bits matter for B300/sm_100 and RTX 50/sm_120: setup.sh
must read the GPU's compute capability and write TORCH_CUDA_ARCH_LIST
(+ VLLM_USE_FLASHINFER_SAMPLER=0 on Blackwell) into the per-instance
vllm-<key>.env. The systemd unit templates must then propagate those
env vars into the apptainer container. And the vLLM SIF recipe must
strip flashinfer at build time so the GPU-side workaround sticks.

We only verify what's host-checkable: bash syntax + that the expected
tokens appear in the right files.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SETUP = REPO / "setup.sh"
VLLM_DEF = REPO / "containers" / "vllm.def"
TMPL_INST = REPO / "systemd" / "vllm@.service.template"
TMPL_SINGLE = REPO / "systemd" / "vllm.service.template"


def test_setup_has_gpu_arch_helper():
    body = SETUP.read_text()
    assert "_gpu_arch()" in body, "setup.sh must define _gpu_arch helper"
    assert "compute_cap" in body, "must query nvidia-smi --query-gpu=compute_cap"


def test_setup_writes_arch_env_into_vllm_env():
    body = SETUP.read_text()
    assert "TORCH_CUDA_ARCH_LIST=$ARCH" in body, \
        "_write_vllm_env must emit TORCH_CUDA_ARCH_LIST"
    # Blackwell-only disable of FlashInfer sampler.
    assert "VLLM_USE_FLASHINFER_SAMPLER=0" in body
    # Must be branched on the arch — not always emitted.
    assert "10.0|10.*|12.0|12.*" in body


def test_systemd_instanced_template_passes_blackwell_env():
    body = TMPL_INST.read_text()
    assert "--env TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}" in body
    assert "--env VLLM_USE_FLASHINFER_SAMPLER=${VLLM_USE_FLASHINFER_SAMPLER}" in body


def test_systemd_single_template_passes_blackwell_env():
    body = TMPL_SINGLE.read_text()
    assert "--env TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}" in body
    assert "--env VLLM_USE_FLASHINFER_SAMPLER=${VLLM_USE_FLASHINFER_SAMPLER}" in body


def test_vllm_def_strips_flashinfer():
    body = VLLM_DEF.read_text()
    assert "pip uninstall -y flashinfer" in body, \
        "vllm.def %post must purge flashinfer-python so Blackwell GPUs work"


def test_setup_stage4_message_guides_to_apt_bundle():
    body = SETUP.read_text()
    assert "apt-get install" in body  # stage 3 still installs from bundle/apt
    # stage 4 must give a concrete recovery hint, not just "fail".
    assert "build-sif-bundle.sh" in body, \
        "stage 4 failure message should point operators at the apt bundle path"
