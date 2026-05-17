"""Configuration models and loaders for LoopCoder.

Three YAML files are loaded and merged in priority order
(plan-level > CLI flag > /etc/loopcoder/loopcoder.yaml > /etc/loopcoder/install.yaml > defaults).
This module covers the loading/merging logic; plan-level overrides are applied
in the Plan parser separately.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------- install.yaml ----------------------------------------------------------------


class DeploymentConfig(BaseModel):
    mode: Literal["offline", "online"] = "offline"
    bundle_root: str = "/models"


class PathsConfig(BaseModel):
    install_root: str = "/scratch/loopcoder"
    model_cache: str = "/scratch/models"
    workspaces_root: str = "/scratch/workspaces"
    log_dir: str = "/var/log/loopcoder"
    state_dir: str = "/var/lib/loopcoder"


class ModelStaging(BaseModel):
    strategy: Literal["rsync", "symlink", "copy"] = "rsync"
    verify_sha256: bool = True


class ModelConfig(BaseModel):
    id: str
    # Optional: legacy bundles set explicit paths. In the SIF-only flow
    # the deploy step transfers the model and setup.sh packs it from
    # --model-src, so these are derived from `id` when omitted.
    source_path: str | None = None
    destination_path: str | None = None
    staging: ModelStaging = ModelStaging()


class ModelEntry(BaseModel):
    """One model in a multi-model deployment.

    Each entry becomes a vllm@<key> systemd instance on its own port.
    Serving params are resolved from the catalog by `id` at install time.
    """

    key: str
    id: str
    port: int


class ContainerConfig(BaseModel):
    """Apptainer .sif locations.

    Production layout uses ``/opt/apptainers/`` as the SIF store with a
    ``current/`` directory of stable symlinks. Upgrades become:

        cp new.sif /opt/apptainers/
        ln -sfn new.sif /opt/apptainers/current/<name>.sif
        systemctl restart <unit>

    The ``vllm_image`` / ``sandbox_image`` / ``suite_image`` fields can
    point at either: (a) absolute paths inside ``current/`` (recommended);
    (b) any other absolute path (e.g. legacy /scratch/loopcoder/containers/).
    """

    vllm_image: str
    sandbox_image: str
    suite_image: str | None = None
    store_dir: str = "/opt/apptainers"
    current_dir: str = "/opt/apptainers/current"


class SystemConfig(BaseModel):
    user: str = "loopcoder"
    group: str = "loopcoder"
    create_user: bool = True


class InstallConfig(BaseModel):
    deployment: DeploymentConfig = DeploymentConfig()
    paths: PathsConfig = PathsConfig()
    # Multi-model (preferred): one vllm@<key> instance per entry.
    models: list[ModelEntry] = Field(default_factory=list)
    default_model: str | None = None
    # Single-model (legacy): used when `models` is empty.
    model: ModelConfig | None = None
    container: ContainerConfig
    system: SystemConfig = SystemConfig()

    @model_validator(mode="after")
    def _need_a_model(self) -> "InstallConfig":
        if not self.models and self.model is None:
            raise ValueError("install config needs either models[] or model")
        if self.models:
            keys = [m.key for m in self.models]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate model keys: {keys}")
            ports = [m.port for m in self.models]
            if len(ports) != len(set(ports)):
                raise ValueError(f"duplicate model ports: {ports}")
            if self.default_model and self.default_model not in keys:
                raise ValueError(
                    f"default_model {self.default_model!r} not in models {keys}"
                )
        return self


# ---------- vllm.yaml ----------------------------------------------------------------


class ServingConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str = ""


class EngineConfig(BaseModel):
    tensor_parallel_size: int = 8
    max_model_len: int = 262144
    max_num_seqs: int = 8
    gpu_memory_utilization: float = 0.92
    quantization: str = "fp8"
    enable_prefix_caching: bool = True
    enable_chunked_prefill: bool = True
    swap_space_gb: int = 0
    kv_cache_dtype: str = "auto"
    trust_remote_code: bool = False


class SystemdConfig(BaseModel):
    restart: str = "on-failure"
    restart_sec: int = 15


class VllmConfig(BaseModel):
    serving: ServingConfig = ServingConfig()
    engine: EngineConfig = EngineConfig()
    env: dict[str, str] = Field(
        default_factory=lambda: {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NCCL_P2P_LEVEL": "NVL",
            "VLLM_USE_V1": "1",
        }
    )
    systemd: SystemdConfig = SystemdConfig()


# ---------- loopcoder.yaml ----------------------------------------------------------------


class LlmRetry(BaseModel):
    max_attempts: int = 5
    backoff_initial_sec: float = 2.0
    backoff_max_sec: float = 60.0


class LlmEndpoint(BaseModel):
    """One servable model: a vLLM instance's URL + served name.

    In multi-model deployments each model key (matching install.yaml's
    models[].key) maps to its own vllm@<key> instance on its own port.
    """

    base_url: str
    model: str
    api_key: str = "EMPTY"


class LlmConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key: str = "EMPTY"
    model: str = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"
    # Optional multi-model routing table: key -> endpoint. A plan can
    # select one via `llm.model: <key>`; unknown values fall back to the
    # single base_url/model above (treated as a literal served name).
    models: dict[str, LlmEndpoint] = Field(default_factory=dict)
    default_model: str | None = None
    temperature: float = 0.2
    top_p: float = 0.95
    max_completion_tokens: int = 8192
    request_timeout_sec: int = 600
    retry: LlmRetry = LlmRetry()

    def resolve_endpoint(self, requested: str | None) -> tuple[str, str, str]:
        """Return (base_url, model, api_key) for a requested model/key.

        Precedence: explicit request key in models[] > default_model in
        models[] > the flat base_url/model (request treated as a literal
        served-model name when given).
        """
        key = requested or self.default_model
        if key and key in self.models:
            e = self.models[key]
            return e.base_url, e.model, e.api_key
        # Not a known key: keep single-endpoint behavior, honoring an
        # explicit served-model name if the request was one.
        return self.base_url, (requested or self.model), self.api_key


class PreserveConfig(BaseModel):
    verify_logs: bool = True
    git_diff: bool = True


class ContextConfig(BaseModel):
    total_budget_tokens: int = 240000
    reserve_for_completion: int = 8192
    always_pin: list[str] = Field(default_factory=lambda: ["README.md"])
    preserve_full: PreserveConfig = PreserveConfig()
    summarize_oldest_when_over_pct: int = 70
    per_file_max_kb: int = 256
    grep_max_results: int = 200
    list_dir_max_depth: int = 3
    list_dir_max_entries: int = 500


class BindMount(BaseModel):
    source: str
    dest: str
    mode: Literal["rw", "ro"] = "rw"


class SandboxConfig(BaseModel):
    backend: Literal["apptainer", "host"] = "apptainer"
    image: str = "/scratch/loopcoder/containers/loopcoder-sandbox.sif"
    bind_mounts: list[BindMount] = Field(
        default_factory=lambda: [
            BindMount(source="{workspace}", dest="/workspace", mode="rw"),
            BindMount(source="/scratch/loopcoder/cache", dest="/cache", mode="rw"),
        ]
    )
    network: bool = False
    read_only_paths: list[str] = Field(default_factory=lambda: ["/etc", "/usr"])
    default_cwd: str = "/workspace"
    exec_timeout_sec: int = 600


class ShellToolConfig(BaseModel):
    allowed_patterns: list[str] = Field(
        default_factory=lambda: [
            "pytest*",
            "python*",
            "python3*",
            "pip*",
            "uv*",
            "ls*",
            "cat *",
            "head *",
            "tail *",
            "grep *",
            "find *",
            "git *",
            "npm test*",
            "cargo test*",
            "go test*",
        ]
    )
    output_max_kb: int = 256
    timeout_sec_default: int = 300


class FsToolConfig(BaseModel):
    forbidden_paths: list[str] = Field(
        default_factory=lambda: [
            "**/.env",
            "**/secrets/**",
            "**/.ssh/**",
            "/etc/**",
        ]
    )
    max_read_bytes: int = 1_048_576


class ToolsConfig(BaseModel):
    shell: ShellToolConfig = ShellToolConfig()
    fs: FsToolConfig = FsToolConfig()


class LoopConfig(BaseModel):
    max_iterations_per_goal: int = 50
    max_total_minutes: int = 360
    strategy_change_after: int = 3
    rollback_after: int = 6
    use_critic: bool = False
    parallel_goals: bool = False


class StorageConfig(BaseModel):
    state_db: str = "/var/lib/loopcoder/sessions.db"
    log_dir: str = "/var/log/loopcoder"
    workspaces_root: str = "/scratch/workspaces"


class UiConfig(BaseModel):
    tty: Literal["rich", "plain"] = "rich"
    log_level: str = "INFO"


class LoopCoderConfig(BaseModel):
    """Top-level agent configuration (loopcoder.yaml)."""

    llm: LlmConfig = LlmConfig()
    context: ContextConfig = ContextConfig()
    sandbox: SandboxConfig = SandboxConfig()
    tools: ToolsConfig = ToolsConfig()
    loop: LoopConfig = LoopConfig()
    storage: StorageConfig = StorageConfig()
    ui: UiConfig = UiConfig()

    @field_validator("llm")
    @classmethod
    def _validate_llm(cls, v: LlmConfig) -> LlmConfig:
        if v.temperature < 0 or v.temperature > 2:
            raise ValueError("llm.temperature must be in [0, 2]")
        return v


# ---------- YAML loading & env expansion -------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} in strings using os.environ."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [expand_env_vars(x) for x in value]
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    return value


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file. Returns {} for missing or empty files."""
    p = Path(path)
    if not p.is_file():
        return {}
    text = p.read_text()
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top-level YAML must be a mapping, got {type(data).__name__}")
    return expand_env_vars(data)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` into ``base``. ``override`` wins on conflicts.

    Lists are *replaced*, not concatenated, to keep behavior predictable.
    """
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------- Public loader helpers --------------------------------------------------------


def _default_install_yaml() -> str:
    return os.environ.get("LOOPCODER_INSTALL_YAML", "/etc/loopcoder/install.yaml")


def _default_vllm_yaml() -> str:
    return os.environ.get("LOOPCODER_VLLM_YAML", "/etc/loopcoder/vllm.yaml")


def _default_loopcoder_yaml() -> str:
    return os.environ.get("LOOPCODER_YAML", "/etc/loopcoder/loopcoder.yaml")


# Back-compat module-level constants (evaluated at import time only).
DEFAULT_INSTALL_YAML = _default_install_yaml()
DEFAULT_VLLM_YAML = _default_vllm_yaml()
DEFAULT_LOOPCODER_YAML = _default_loopcoder_yaml()


def load_install_config(path: str | Path | None = None) -> InstallConfig:
    chosen = str(path) if path else _default_install_yaml()
    raw = load_yaml(chosen)
    if not raw:
        raise FileNotFoundError(
            f"install.yaml not found at {chosen}. "
            "Run setup.sh or pass --config explicitly."
        )
    return InstallConfig.model_validate(raw)


def load_vllm_config(path: str | Path | None = None) -> VllmConfig:
    raw = load_yaml(str(path) if path else _default_vllm_yaml())
    return VllmConfig.model_validate(raw or {})


def load_loopcoder_config(
    path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> LoopCoderConfig:
    """Load loopcoder.yaml and apply optional overrides (e.g. from CLI)."""
    raw = load_yaml(str(path) if path else _default_loopcoder_yaml())
    if overrides:
        raw = deep_merge(raw, overrides)
    return LoopCoderConfig.model_validate(raw or {})


def merged_view(
    install: InstallConfig | None = None,
    vllm: VllmConfig | None = None,
    loopcoder: LoopCoderConfig | None = None,
) -> dict[str, Any]:
    """Return a merged dict view of the three configs for `loopcoder config show`."""
    out: dict[str, Any] = {}
    if install is not None:
        out["install"] = install.model_dump()
    if vllm is not None:
        out["vllm"] = vllm.model_dump()
    if loopcoder is not None:
        out["loopcoder"] = loopcoder.model_dump()
    return out
