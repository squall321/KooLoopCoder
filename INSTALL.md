# LoopCoder — Install Guide

## Prerequisites

**Bundle host** (Ubuntu 22.04 in this project):
- virt-manager / libvirt / KVM (`apt install virt-manager qemu-kvm libvirt-daemon-system`)
- xorriso *or* genisoimage (`apt install xorriso`)
- ssh, rsync, curl, sha256sum

**B300 deployment node** (Ubuntu 24.04, NO internet):
- NVIDIA driver + CUDA ≥ 12.8 (Blackwell required)
- 8× B300 GPUs
- ≥ 1 TB free at `/scratch`
- A read-only mount at `/models` containing the bundle

## End-to-end procedure

### 1. Bundle build (on the 22.04 host with internet)

```bash
git clone <this-repo> LoopCoder
cd LoopCoder
bash bundle.sh                                     # builds bundle in LoopCoder/output/bundle/
```

This:
- Creates a Bundle VM (Ubuntu 24.04) under `/data/loopcoder-vm/`.
- Inside the VM, downloads .deb packages, Python wheels, the vLLM Docker
  image (converted to Apptainer .sif), the sandbox .sif, and model weights.
- Writes manifest.yaml + manifest.sha256 to `LoopCoder/output/bundle/`.

### 2. Test the bundle (still on the 22.04 host)

```bash
bash test_setup.sh                                 # spins up Test VM, runs setup.sh --skip-gpu-stages
```

The Test VM has no internet and no GPU, so it exercises stages 0, 2-9, 12,
13 (i.e. everything except GPU verification, vLLM start, and smoke).

A markdown report lands in `/data/loopcoder-test-results/`.

### 3. Transfer to B300

```bash
rsync -avP LoopCoder/output/bundle/ b300:/models/
```

### 4. Install on B300 (offline)

On the B300 node:

```bash
# /models is now populated with the bundle
sudo cp /models/source/LoopCoder/config/install.yaml.example  /etc/loopcoder/install.yaml
sudo cp /models/source/LoopCoder/config/vllm.yaml.example     /etc/loopcoder/vllm.yaml
sudo cp /models/source/LoopCoder/config/loopcoder.yaml.example /etc/loopcoder/loopcoder.yaml
# review/edit these as needed
sudo bash /models/source/LoopCoder/setup.sh
```

setup.sh runs all 14 stages including GPU verification, model staging to
`/scratch/models/`, vLLM systemd service start, and a smoke test.

### 5. First run

```bash
loopcoder run --plan /models/source/LoopCoder/examples/plan_simple.yaml
```

## Day-to-day commands

```bash
loopcoder list                       # past sessions
loopcoder status [SESSION_ID]        # progress / state
loopcoder report SESSION_ID > out.md
loopcoder tokens SESSION_ID
loopcoder config validate
loopcoder config show                # merged config
```

## Resume / re-run

```bash
sudo bash setup.sh                   # idempotent: skips completed stages
sudo bash setup.sh --stage 7         # force from a specific stage
sudo bash setup.sh --reinstall       # wipe markers, redo all
sudo bash setup.sh --uninstall       # remove install (keeps model cache)
```

## Editing config

Run `loopcoder config validate` after every change. Most changes (model
parameters, sandbox bind mounts, allowed shell patterns) take effect on the
next `loopcoder run`. Changes to `vllm.yaml` need
`systemctl restart vllm`.

## Troubleshooting

- vLLM not coming up → `journalctl -u vllm -n 200`
- "must read_file before edit" → make sure the agent reads a file first;
  this is a guardrail, not a bug.
- Apptainer build fails on Bundle VM → check `docker.io` is running and
  the `vllm/vllm-openai:latest` image is reachable.
- Test VM gets internet (assertion fails) → run
  `virsh net-edit loopcoder-test-isolated` and confirm `forward mode='none'`.
