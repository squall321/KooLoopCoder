"""Model catalog + hardware-based recommendation.

Reads ``config/model-catalog.yaml`` and answers:

  - "for hardware profile X, which models fit?"
  - "for hardware profile X, what's the best (largest fitting) model?"
  - "how big is model Y? does it fit on X?"

Used by ``loopcoder select-model`` and by the Windows-side deploy
helpers that pick a model before downloading from HuggingFace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pydantic import BaseModel, Field


class HardwareProfile(BaseModel):
    gpus: int
    per_gpu_gb: float
    total_vram_gb: float
    practical_budget_gb: float
    notes: str = ""


class CatalogModel(BaseModel):
    id: str
    params_b: float
    active_b: float | None = None
    quant: str
    approx_vram_gb: float
    license: str = ""
    tp_default: int = 1
    max_model_len: int = 32768
    is_coder: bool = False
    is_moe: bool = False
    requires_hf_token: bool = False
    # vLLM serving knobs derivable from the model alone. quant maps to
    # vLLM's --quantization (bf16 -> none, i.e. don't pass the flag).
    # tool_call_parser is the vLLM --tool-call-parser; Qwen ships the
    # hermes <tool_call> template, so that's the sane default.
    tool_call_parser: str = "hermes"
    recommended_for: list[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def vllm_quantization(self) -> str:
        """vLLM --quantization value, or '' when the model is unquantized."""
        q = (self.quant or "").lower()
        if q in ("", "bf16", "fp16", "none", "float16", "bfloat16"):
            return ""
        if q == "awq":
            return "awq_marlin"
        return q  # fp8, gptq, awq_marlin, etc. pass through


class Catalog(BaseModel):
    hardware_profiles: dict[str, HardwareProfile]
    models: list[CatalogModel]
    recommendations: dict[str, str] = Field(default_factory=dict)

    def profile(self, name: str) -> HardwareProfile:
        if name not in self.hardware_profiles:
            raise KeyError(f"unknown hardware profile: {name}")
        return self.hardware_profiles[name]

    def model(self, model_id: str) -> CatalogModel:
        for m in self.models:
            if m.id == model_id:
                return m
        raise KeyError(f"unknown model: {model_id}")

    def fits(self, model: CatalogModel, profile: HardwareProfile) -> bool:
        return model.approx_vram_gb <= profile.practical_budget_gb

    def fitting_models(self, profile_name: str) -> list[CatalogModel]:
        p = self.profile(profile_name)
        return [m for m in self.models if self.fits(m, p)]

    def best_for(self, profile_name: str) -> CatalogModel:
        """Return the largest model that fits on the given profile.

        If `recommendations` explicitly names one for this profile and that
        model is in the catalog, prefer it (curated choice).
        """
        if profile_name in self.recommendations:
            mid = self.recommendations[profile_name]
            try:
                return self.model(mid)
            except KeyError:
                pass
        fitting = self.fitting_models(profile_name)
        if not fitting:
            raise RuntimeError(f"no models fit profile {profile_name!r}")
        return max(fitting, key=lambda m: m.approx_vram_gb)


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "model-catalog.yaml"


def load_catalog(path: str | Path | None = None) -> Catalog:
    p = Path(path) if path else default_catalog_path()
    if not p.is_file():
        raise FileNotFoundError(f"catalog not found: {p}")
    raw = yaml.safe_load(p.read_text())
    return Catalog.model_validate(raw)


# Convenience for shell scripts:
def recommend_cli() -> int:
    """Entrypoint for ``loopcoder select-model``."""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        description="Pick the largest LoopCoder-compatible model for a hardware profile.",
    )
    ap.add_argument("profile", help="Hardware profile name (e.g. b300x8).")
    ap.add_argument("--catalog", default=None, help="Override catalog YAML path.")
    ap.add_argument("--list", action="store_true", help="List all fitting models, not just the best.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = ap.parse_args()

    try:
        cat = load_catalog(args.catalog)
    except FileNotFoundError as e:
        print(f"FAIL  {e}", file=sys.stderr)
        return 2

    if args.profile not in cat.hardware_profiles:
        print(f"unknown profile: {args.profile!r}", file=sys.stderr)
        print(f"known: {', '.join(sorted(cat.hardware_profiles))}", file=sys.stderr)
        return 2

    p = cat.profile(args.profile)
    if args.list:
        models = cat.fitting_models(args.profile)
    else:
        try:
            models = [cat.best_for(args.profile)]
        except RuntimeError as e:
            print(f"FAIL  {e}", file=sys.stderr)
            return 3

    if args.json:
        print(json.dumps({
            "profile": args.profile,
            "profile_info": p.model_dump(),
            "models": [m.model_dump() for m in models],
        }, indent=2))
    else:
        print(f"# Profile {args.profile}: {p.gpus} GPU × {p.per_gpu_gb} GiB"
              f"  (budget ≈ {p.practical_budget_gb} GiB)")
        for m in models:
            star = "★ " if not args.list else "  "
            print(f"{star}{m.id}")
            print(f"    weights ≈ {m.approx_vram_gb} GiB  ({m.quant}, "
                  f"params {m.params_b}B" + (f", active {m.active_b}B" if m.active_b else "") + ")")
            print(f"    tp_default={m.tp_default}  max_model_len={m.max_model_len}")
            if m.notes:
                print(f"    note: {m.notes}")
    return 0


def resolve_model(model_id: str, catalog_path: str | Path | None = None) -> dict[str, Any]:
    """Derive everything needed to serve `model_id` from its name alone.

    Looks the model up in the catalog; if absent, falls back to id-based
    heuristics so an operator can still name an arbitrary HF repo in
    install.yaml without editing serving flags by hand.
    """
    leaf = model_id.rsplit("/", 1)[-1]
    try:
        cat = load_catalog(catalog_path)
        m = cat.model(model_id)
        quant = m.vllm_quantization
        tp = m.tp_default
        max_len = m.max_model_len
        parser = m.tool_call_parser
        known = True
    except (FileNotFoundError, KeyError):
        # Heuristics from the repo name.
        low = model_id.lower()
        if "fp8" in low:
            quant = "fp8"
        elif "awq" in low:
            quant = "awq_marlin"
        elif "gptq" in low:
            quant = "gptq_marlin"
        else:
            quant = ""
        tp = 1
        max_len = 32768
        parser = "hermes"
        known = False
    return {
        "id": model_id,
        "leaf": leaf,
        "known_in_catalog": known,
        "quantization": quant,           # "" => don't pass --quantization
        "tensor_parallel_size": tp,
        "max_model_len": max_len,
        "tool_call_parser": parser,
    }


def resolve_cli() -> int:
    """Entrypoint for ``loopcoder catalog-resolve <model_id> [--json]``.

    setup.sh calls this to turn a single model id in install.yaml into
    concrete vLLM serving flags. Shell-friendly KEY=VALUE by default.
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="Resolve a model id to vLLM serving parameters.",
    )
    ap.add_argument("model_id", help="HuggingFace repo id (e.g. Qwen/Qwen2.5-Coder-7B-Instruct-AWQ).")
    ap.add_argument("--catalog", default=None, help="Override catalog YAML path.")
    ap.add_argument("--json", action="store_true", help="JSON instead of KEY=VALUE.")
    args = ap.parse_args()

    info = resolve_model(args.model_id, args.catalog)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        # Shell-eval friendly: MODEL_QUANTIZATION=fp8 etc.
        print(f"MODEL_ID={info['id']}")
        print(f"MODEL_LEAF={info['leaf']}")
        print(f"MODEL_KNOWN={'1' if info['known_in_catalog'] else '0'}")
        print(f"MODEL_QUANTIZATION={info['quantization']}")
        print(f"MODEL_TP={info['tensor_parallel_size']}")
        print(f"MODEL_MAX_LEN={info['max_model_len']}")
        print(f"MODEL_TOOL_PARSER={info['tool_call_parser']}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(recommend_cli())
