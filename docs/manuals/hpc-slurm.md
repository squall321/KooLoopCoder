# Running LoopCoder on an HPC cluster (Slurm, Apptainer, no root)

> Scenario: a shared HPC cluster. **No sudo, no systemd, no writable
> `/opt` `/var` `/etc`.** Compute nodes have GPUs + apptainer but
> usually no internet. Jobs run under **Slurm** (`sbatch`/`srun`).
>
> LoopCoder runs entirely from `$LOOPCODER_HOME` (your `$SCRATCH` or
> `$HOME`) using only pre-built SIFs. Nothing is installed system-wide.

This is a *parallel* path to the B300/systemd deploy — that one is
untouched. Use whichever matches your environment.

---

## 1. Big picture

```
[build host, internet + apptainer]            [HPC login node]            [HPC GPU compute node]
  scripts/build-sif-bundle.sh                   loopcoder-hpc.sh             (Slurm allocation)
   -> vllm.sif                                    init                        apptainer run vllm.sif
   -> loopcoder-suite.sif        copy SIFs +      submit-allinone  --sbatch-> apptainer exec suite.sif
   -> loopcoder-sandbox.sif      model + yaml     submit-serve                  loopcoder run ...
  + fetch model dir   ───────────────────────►  $LOOPCODER_HOME/...
```

The build host work is unchanged (`build-sif-bundle.sh`,
`fetch-models.sh`). Only the *run* side differs: Slurm jobs instead of
systemd services.

---

## 2. One-time setup on the cluster

```bash
# Pick a state root on a filesystem you can write to (scratch is ideal).
export LOOPCODER_HOME=$SCRATCH/loopcoder      # or ~/.loopcoder

git clone https://github.com/squall321/KooLoopCoder
bash KooLoopCoder/scripts/hpc/loopcoder-hpc.sh init
```

`init` creates the layout and prints exactly what to copy in:

```
$LOOPCODER_HOME/
  sif/   vllm.sif  loopcoder-suite.sif  loopcoder-sandbox.sif
  models/<leaf>/                 unpacked HF model dir  (or model-<leaf>.sif)
  etc/   install.yaml            models[] or a single model.id
         loopcoder.yaml          llm.base_url = http://127.0.0.1:8000/v1
  cache/ logs/ workspaces/ state/
```

Transfer the SIFs + model + the two YAMLs from your build host
(`scp`/`rsync`/Globus — whatever your site uses). No build happens on
the cluster.

---

## 3. Run a plan (all-in-one job)

One Slurm job brings up vLLM, runs the plan, and exits (frees the GPU):

```bash
bash scripts/hpc/loopcoder-hpc.sh submit-allinone plan.yaml \
    --model fast --partition gpu --gpus 1 --time 02:00:00
```

What it does inside the allocation (`_allinone-inproc`):
1. `apptainer run vllm.sif …` in the background (serving params —
   quantization / tensor-parallel / max-len / tool-parser — resolved
   from the catalog by model id; Blackwell/sm_120 FlashInfer
   workaround applied, harmless elsewhere).
2. Waits for `:8000/v1/models`.
3. `apptainer exec loopcoder-suite.sif loopcoder run --plan …`.
4. Stops vLLM, job exits with loopcoder's return code.

Model weights bind from `$LOOPCODER_HOME/models/`: a packed
`model-<leaf>.sif` (`--bind …:image-src=/`) if present, else the
unpacked `<leaf>/` directory.

---

## 4. Long-lived serving job (multi-user / reuse)

```bash
bash scripts/hpc/loopcoder-hpc.sh submit-serve \
    --model big --partition gpu --gpus 8 --time 12:00:00
```

The job prints the compute node's `IP:port`. Point a login-node (or
another job's) `loopcoder.yaml` `llm.base_url` at it, or SSH-tunnel:

```bash
ssh -L 8000:<compute-node>:8000 login-node
# then loopcoder.yaml: llm.base_url: http://127.0.0.1:8000/v1
```

Multi-model: list models[] in `install.yaml` (same schema as
deploy.yaml). `--model <key>` selects which one a job serves/uses;
omit it to use `default_model`.

---

## 5. Interactive (debug, inside salloc)

```bash
salloc --partition gpu --gres gpu:1 --time 01:00:00
# on the allocated node:
bash scripts/hpc/loopcoder-hpc.sh _serve-inproc &     # vLLM
bash scripts/hpc/loopcoder-hpc.sh run plan.yaml       # loopcoder
```

---

## 6. Tunables (env or flags)

| Env | Flag | Default | Meaning |
|---|---|---|---|
| `LOOPCODER_HOME` | — | `$SCRATCH/loopcoder` or `~/.loopcoder` | state root |
| `LOOPCODER_PARTITION` | `--partition` | `gpu` | Slurm partition |
| `LOOPCODER_GPUS` | `--gpus` | `1` | `--gres=gpu:N` |
| `LOOPCODER_TIME` | `--time` | `04:00:00` | walltime |
| `LOOPCODER_MODEL` | `--model` | (default_model) | models[] key |
| `LOOPCODER_VLLM_PORT` | — | `8000` | vLLM port |
| `TORCH_CUDA_ARCH_LIST` | — | unset | set `12.0` for Blackwell if needed |

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `apptainer not found on this node` | Load it: `module load apptainer` in your job (add to the rendered sbatch if your site needs it) |
| `missing …/sif/vllm.sif` | Copy the SIFs into `$LOOPCODER_HOME/sif/` (build on a host with apptainer) |
| `model not found: neither …model-<leaf>.sif nor …<leaf>/` | Copy the model under `$LOOPCODER_HOME/models/` |
| `no $…/etc/install.yaml` | Copy install.yaml (with models[] or model.id) into `$LOOPCODER_HOME/etc/` |
| vLLM dies immediately on Blackwell GPUs | `export TORCH_CUDA_ARCH_LIST=12.0` before submit |
| `(no sbatch here)` | You're not on a Slurm host; the job script is printed/saved under `logs/` to inspect or run elsewhere |
| GPU model wraps tool calls in markdown | Already handled — the suite SIF's content-fallback parser recovers them |
