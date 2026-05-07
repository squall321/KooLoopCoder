# One-command deploy to a B300 (or any 24.04 GPU host)

> Goal: from any machine that has SSH passwordless access to the GPU
> server, deploy LoopCoder with **one command and one YAML file**. The
> GPU host needs nothing pre-installed except the NVIDIA driver and
> SSH server (`apt install openssh-server`). Apptainer, vLLM, the agent,
> and the systemd units all come from the bundle.

---

## 1. Inputs

You need three things on the **machine running deploy** (NOT the GPU host):

1. **Bundle directory** — produced by `bundle.sh` (or the noble VM
   workflow). Contains `apt/`, `wheels/`, `containers/`, `source/`,
   `manifest.sha256`. Default location: `LoopCoder/output/bundle/` or
   `/data/loopcoder-bundle/`.
2. **Passwordless SSH** to the target. Test with:
   ```bash
   ssh user@b300 'echo ok'
   ```
3. **A `deploy.yaml`** — see `config/deploy.yaml.example`. Copy and edit:
   ```yaml
   target:
     host: ubuntu@b300.example.org
     remote_bundle: /models
   bundle:
     local_dir: /data/loopcoder-bundle
   flags:
     skip_gpu_stages: false
     skip_model_stage: false
   model:
     mode: rsync                              # none | rsync | hf
     local_path: /data/models/Qwen2.5-Coder-1.5B-Instruct
     remote_path: /scratch/models/Qwen2.5-Coder-1.5B-Instruct
   ```

---

## 2. Run it

```bash
bash scripts/deploy.sh --config deploy.yaml
```

That's it. The script does, in order:

1. **Local manifest verify** — refuses to deploy a corrupted bundle.
2. **Remote preflight** — `lsb_release`, `uname`, free space.
3. **rsync bundle → /models** on the target (idempotent; skips unchanged).
4. **`apt-get install -y --no-install-recommends ./apt/*.deb`** on the
   target — installs Apptainer + every transitive dep. **Uses apt, not
   `dpkg -i`**, so the dependency graph is resolved across the staged
   `.deb` files.
5. **Model staging** (if YAML says so):
   * `mode: rsync` — `rsync local_path → user@host:remote_path`.
   * `mode: hf` — `huggingface-cli download` runs **on the target**
     (target needs internet — usually NOT the B300 case).
   * `mode: none` — skip; model is already on the target via OOB upload.
6. **`bash setup.sh`** on the target — 14-stage offline install:
    - GPU detection (skip with `flags.skip_gpu_stages` for Test VMs)
    - manifest sha256 verify
    - python3.12 venv build from wheels
    - SIFs into `/opt/apptainers/` + `current/` symlinks
    - systemd `vllm.service` + `loopcoder.service` enable
    - vLLM smoke test (`/v1/chat/completions` returns "2" for "1+1=")

After completion:

```bash
ssh user@b300 'systemctl status vllm loopcoder; loopcoder --version'
```

The HTTP API is at `http://127.0.0.1:8765` on the target (loopback).
Tunnel from your IDE machine via `ssh -L 8765:127.0.0.1:8765 b300` and
the LoopCoder VS Code extension lights up.

---

## 3. Sample-model first, big-model later

Recommended progression — **catch problems with a tiny model before
spending bandwidth on the 480GB target model**:

### 3.1 Pack a small model on your dev machine

```bash
bash scripts/pack-model.sh --hf Qwen/Qwen2.5-Coder-1.5B-Instruct \
                           qwen-1.5b.sif

# or from an already-downloaded directory:
bash scripts/pack-model.sh /scratch/models/Qwen2.5-Coder-1.5B-Instruct \
                           qwen-1.5b.sif
```

Result: a single ~3 GB SIF you can `scp` anywhere. (Why pack? Single
file is trivial to move and idempotent to verify.)

### 3.2 Deploy with the tiny model

```yaml
# deploy-sample.yaml
target:
  host: ubuntu@b300
model:
  mode: rsync
  local_path: /data/models/Qwen2.5-Coder-1.5B-Instruct
  remote_path: /scratch/models/Qwen2.5-Coder-1.5B-Instruct
```

```bash
bash scripts/deploy.sh --config deploy-sample.yaml
```

Verify on B300:

```bash
ssh b300 'curl -sf http://127.0.0.1:8000/v1/models'
ssh b300 "curl -sf -H 'content-type: application/json' \
  http://127.0.0.1:8000/v1/chat/completions \
  -d '{\"model\":\"Qwen/Qwen2.5-Coder-1.5B-Instruct\",
       \"messages\":[{\"role\":\"user\",\"content\":\"What is 1+1?\"}]}'"
```

If that's good, the entire stack — driver → Apptainer → vLLM → API →
MCP — is alive. Now upgrade the model.

### 3.3 Swap in the big model

After the tiny model works:

```bash
# Option A — rsync big model from your machine to b300
bash scripts/deploy.sh --config deploy-big.yaml --setup-only=false

# Option B — download on b300 directly (only works if b300 has internet)
# In deploy-big.yaml:
model:
  mode: hf
  hf_id: Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8
  remote_path: /scratch/models/Qwen3-Coder-480B-A35B-Instruct-FP8
```

Then update `install.yaml` on the target to point at the new model id
and `systemctl restart vllm`.

---

## 4. What scripts/deploy.sh accepts

| Flag | Equivalent YAML | Meaning |
|---|---|---|
| `user@host` (positional) | `target.host` | SSH destination |
| `--config <file>` | — | Read YAML for everything below |
| `--bundle <dir>` | `bundle.local_dir` | Source bundle on the deploy host |
| `--remote-bundle <path>` | `target.remote_bundle` | Where it lands on the target (default `/models`) |
| `--skip-gpu-stages` | `flags.skip_gpu_stages` | Test VMs (no nvidia-smi) |
| `--skip-model-stage` | `flags.skip_model_stage` | Don't stage `/scratch/models` |
| `--apt-only` | — | Stop after `apt install`; don't run setup.sh |
| `--setup-only` | — | Bundle is already on target; just run setup.sh |
| `--dry-run` | — | Print everything; do nothing |
| `--no-sudo` | `target.sudo_remote: ""` | Target has root login (rare) |
| `--ssh-opt X` | `target.ssh_opts` | Extra `ssh` flag (e.g. `-J jump@host`) |

CLI flags **win over** YAML when both are given. So the same YAML can be
reused with overrides:

```bash
bash scripts/deploy.sh --config deploy.yaml --skip-gpu-stages
```

---

## 5. Idempotency & resume

Everything is idempotent:

| Step | How |
|---|---|
| rsync | only sends changed files |
| `apt install` | apt skips already-installed packages |
| `setup.sh` | per-stage marker files; skips completed stages unless `--reinstall` |
| `pack-model.sh` | refuses to overwrite without explicit `--force` (TODO) |
| symlink upgrades | `ln -sfn` is atomic |

If a step fails midway, just rerun `bash scripts/deploy.sh --config X`.

---

## 6. Disaster recovery / rollback

### Rollback after a bad upgrade

```bash
# On the GPU host (after a bad bundle was pushed):
sudo bash /models/source/LoopCoder/scripts/upgrade-suite.sh \
    /opt/apptainers/loopcoder-suite-OLD.sif \
    loopcoder-suite.sif
# (which is just: cp + ln -sfn + systemctl restart)
```

### Tear down completely

```bash
ssh b300 'sudo bash /models/source/LoopCoder/scripts/uninstall.sh'
```

Models in `/scratch/models/` are kept by default. Pass `--purge-data`
to wipe everything including model weights.
