# LLM 추론 스택 + 자동 코딩 에이전트 구축 계획서

> **작성일**: 2026-05-05 (rev. 2)
> **프로젝트 코드명**: **LoopCoder** (Python 패키지·CLI는 소문자 `loopcoder`)
> **목표**: B300x8 GPU 서버(인터넷 불가)에 최신 오픈소스 LLM을 깔고, 사용자가 작성한 plan에 따라
> 모든 goal이 검증 통과할 때까지 무한 디버깅하며 코드를 작성/수정하는 에이전트를 구축한다.
> **운영환경**: B300 서버는 외부망 차단. 모든 의존성은 인터넷 가능한 머신에서 번들링 후 전송.
> **범위**: 본 문서는 요구사항 #1(범용 코딩 에이전트)에만 한정. 요구사항 #2(LSDyna 자동 디버깅)는
> CPU 노드/Slurm 준비 후 별도 계획으로 분리. 단, #2 확장이 쉽도록 도구·루프 구조를 설계.

---

## 0. 철칙 (Operating Rules) — 항상 적용

> 이 절은 본 프로젝트 진행 중 **위반 불가**한 운영 원칙이다.
> Claude/사람 모두 매 작업마다 점검한다.

### 0.1 실시간 구현현황 갱신 (★ 가장 중요)
- **모든 파일 생성·수정·삭제 직후 `PROGRESS.md`를 즉시 갱신한다.**
- "다 만들고 나서 한꺼번에 업데이트"는 금지. **단위 작업이 끝날 때마다 갱신.**
- 갱신 항목: 산출물 경로, 상태(`☐ todo` / `▣ in-progress` / `■ done` / `✗ failed`), 검증 결과, 타임스탬프, 다음 액션
- `PROGRESS.md`와 실제 파일 상태가 어긋나면 작업 중단, 동기화 후 재개
- 검증 통과한 경우만 `■ done`. "만들기만 했고 안 돌려봤음"은 절대 done 아님 (`▣ in-progress` 또는 `△ untested`)

### 0.2 검증 없이 완료 선언 금지
- 모든 구현은 **명령으로 검증한 결과**가 있어야 done
- 검증 명령과 결과(stdout/exit code)를 `PROGRESS.md` 또는 그 옆 로그에 기록
- 사람에게 "다 됐다"고 말하기 전에 PROGRESS.md의 해당 항목이 `■ done`인지 본인이 먼저 확인

### 0.3 하드코딩·미완성 위장 금지
- "TODO", "임시", "나중에" 주석으로 미완성 봉합 금지
- 더미 함수, 빈 구현, mock-only 모듈은 명시적으로 `▣ in-progress` 표시
- 한 번에 다 못 만들면 그 사실을 PROGRESS.md에 정직히 기록 (감추지 말기)

### 0.4 단계 게이팅
- `PROGRESS.md`의 이전 단계가 `■ done`이 아니면 다음 단계 시작 금지
- 예외: 명시적 병렬 가능 항목 (PLAN.md에 표시)

### 0.5 PLAN.md ↔ PROGRESS.md 일관성
- PLAN.md에 없는 산출물을 만들지 않는다. 추가 필요시 먼저 PLAN.md를 갱신
- PLAN.md 변경 시 PROGRESS.md의 해당 항목도 동기화

### 0.6 변경 이력 보존
- PLAN.md 끝의 "변경 이력" 항상 갱신
- PROGRESS.md는 변경 시각을 항목별로 기록 (덮어쓰기 X, append-only history)

---

## 1. 시스템 개요

### 1.1 하드웨어/환경
- GPU: NVIDIA B300 × 8 (Blackwell Ultra, HBM3e 288GB/장 → **총 2,304GB**)
- OS: Ubuntu 24.04 LTS, kernel 6.8.0-107-generic
- Driver/CUDA: 최신 설치 완료(가정). setup.sh에서 검증:
  - `nvidia-smi` GPU 8장 인식
  - CUDA Runtime ≥ 12.8 (Blackwell 필수)
  - Compute Capability 10.0 (sm_100) 이상
- **인터넷**: B300 서버 **불가**. 별도 인터넷 가능 머신에서 번들 후 전송
- **컨테이너 런타임**: Apptainer (Docker 아님)
- **스토리지**:
  - 프로젝트 NFS/공유: `/models` (원본 모델·번들 보관, 읽기)
  - 노드 로컬 NVMe: `/scratch` (실제 추론용 배치 위치, 빠름)

### 1.2 논리 아키텍처

```
사용자 ─ plan.yaml ─▶ loopcoder CLI
                       │
                       ▼
              ┌────────────────────┐
              │ Plan Parser        │
              │ Loop Controller    │◀──┐
              │ Tool Executor      │   │ 검증 실패시 재시도
              │ Goal Verifier      │───┤
              │ Snapshot/Rollback  │   │
              └────────┬───────────┘   │
                       │ OpenAI API    │
                       ▼               │
              ┌────────────────────┐   │
              │ vLLM (systemd)     │───┘
              │  Apptainer .sif    │
              │  TP=8, FP8         │
              │  port 8000         │
              └────────────────────┘
                       ▲
                       │ 모델 가중치
              ┌────────────────────┐
              │ /scratch/models/   │ ◀── (배치 시 /models → 복사)
              └────────────────────┘
```

### 1.3 디스크 레이아웃 (B300 서버 기준)

| 경로 | 용도 | 예상 크기 | 비고 |
|---|---|---|---|
| `/models/` | 프로젝트 공유, 모델·번들 원본 | 1–2 TB | 읽기 위주, NFS 가능 |
| `/scratch/loopcoder/` | 설치 루트, 컨테이너, venv | ~30 GB | 빠른 NVMe 권장 |
| `/scratch/models/` | **로컬 모델 캐시 (vLLM 사용)** | 500GB ~ 1.5TB | 추론용 |
| `/scratch/workspaces/` | 에이전트 작업 공간 | 가변 | 사용자 코드 |
| `/scratch/cache/` | pip wheelhouse, 빌드 캐시 | 5–20 GB | |
| `/var/log/loopcoder/` | 로그 | 회전 후 ~5GB | |
| `/etc/loopcoder/` | 설정 파일 (yaml) | < 1 MB | |
| `/var/lib/loopcoder/` | SQLite 세션 DB | 가변 | |

> 모든 경로는 `config/install.yaml`로 오버라이드 가능. 위 표는 디폴트.

---

## 2. 결정사항 (Decision Log)

| # | 항목 | 값 | 상태 |
|---|---|---|---|
| D1 | 추론 모델 | **Qwen3-Coder-480B-A35B-Instruct (FP8)** | ✅ 확정 |
| D2 | 추론 엔진 | vLLM ≥ 0.7 (Apptainer .sif로 패키징) | ✅ 확정 |
| D3 | 양자화 | FP8 | ✅ 확정 |
| D4 | 모델 원본 | `/models/<model_id>/` (서버에 미리 비치) | ✅ 확정 |
| D5 | 모델 배치(런타임) | `/scratch/models/<model_id>/` (디폴트) | ✅ 확정 |
| D6 | 에이전트 구현 | 자체 Python 구현 | ✅ 확정 |
| D7 | 샌드박스 백엔드 | **Apptainer** | ✅ 확정 |
| D8 | 서비스 관리 | systemd (vllm.service, loopcoder-* 옵션) | ✅ 확정 |
| D9 | HF 토큰 | 없음 → 게이트 없는 모델만 사용 | ✅ 확정 |
| D10 | B300 인터넷 | 없음 → 오프라인 번들 필수 | ✅ 확정 |
| D11 | 컨텍스트 길이 | 256K (max-model-len). 처리량 우선시 64K로 다운샘플 가능 | ✅ 확정 |
| D12 | 동시성 | 단일 사용자 가정, max_num_seqs=8 | ✅ 확정 |
| D13 | 모든 옵션 외부화 | YAML(`/etc/loopcoder/*.yaml`) | ✅ 확정 |
| D14 | 컨텍스트 활용 | "최대한 보존" 전략 (요약 최소화) | ✅ 확정 |
| D15 | 프로젝트 코드명 | **LoopCoder** / `loopcoder` (패키지·CLI) | ✅ 확정 |
| D16 | 모델 변경 시 정책 | install.yaml의 `model.id` 변경 + 번들 재생성 | ✅ 확정 |
| D17 | 번들 호스트 | 이 개발 머신(Ubuntu 22.04, virt-manager 설치됨) | ✅ 확정 |
| D18 | 번들 빌드 환경 | **Ubuntu 24.04 VM (virt-manager/libvirt/KVM)** — 호스트와 타겟 OS 불일치 회피 | ✅ 확정 |
| D19 | VM 출력 공유 방식 | virtiofs 공유 디렉토리 (호스트로 직접 출력) | ✅ 확정 |
| D20 | **Setup 검증 VM** | Test VM(24.04, 인터넷차단, GPU없음)에서 setup.sh 자동 검증 | ✅ 확정 |
| D21 | VM 디스크 루트 | `LoopCoder/output/vm-disks/` (env/CLI로 override 가능) | ✅ 확정 |
| D22 | 번들 출력 루트 | `LoopCoder/output/bundle/` (Test VM이 `/models`로 마운트) | ✅ 확정 |
| D23 | Test VM 작업 영역 | `LoopCoder/output/test-scratch/` (`/scratch` 시뮬레이션) | ✅ 확정 |
| D24 | 테스트 결과 출력 | `LoopCoder/output/test-results/` (markdown 리포트) | ✅ 확정 |
| D25 | 작은 모델 dev 검증 | `LoopCoder/output/tiny-test/` + Qwen2.5-Coder-0.5B-Instruct | ✅ 확정 |

> Qwen3-Coder-480B-A35B-Instruct는 게이트 없음(공개) 확인 후 확정. 만약 변경되어 토큰이 필요해지면 대안 모델(예: DeepSeek-V3 비게이트 변형)로 자동 폴백 정책 추가.

---

## 3. 오프라인 번들 워크플로우 (인터넷 불가 환경의 핵심)

### 3.1 두 단계 워크플로우

```
[인터넷 가능 머신]                       [B300 서버 (오프라인)]
   bundle.sh                                  setup.sh
      │                                          ▲
      ▼                                          │
   /models (또는 외장디스크)  ───── 전송 ────►  /models
   ├── containers/                              ├── containers/
   │    └── vllm-0.7.x.sif                      │    └── vllm-0.7.x.sif
   ├── wheels/                                  ├── wheels/
   │    └── *.whl                               │    └── *.whl
   ├── apt/                                     ├── apt/
   │    └── *.deb                               │    └── *.deb
   ├── models/                                  ├── models/
   │    └── Qwen3-Coder-480B-FP8/               │    └── Qwen3-Coder-480B-FP8/
   ├── source/                                  ├── source/
   │    └── loopcoder/                            │    └── loopcoder/
   └── manifest.yaml (해시·버전)                  └── manifest.yaml
```

### 3.2 `bundle.sh` (인터넷 가능 머신에서 실행)

수행 작업:
1. **vLLM 컨테이너**: NGC 또는 vLLM 공식 docker 이미지를 pull → `apptainer build vllm.sif docker-daemon://...` 또는 `docker save | apptainer build`
2. **Python wheels**: `pip download -r requirements.txt -d wheels/ --platform manylinux_2_28_x86_64 --python-version 3.12`
3. **APT 패키지**: `apt-get download` 으로 .deb 수집 (의존성 트리 포함)
4. **모델 다운로드**: `huggingface-cli download <model_id> --local-dir models/<model_id>` (`HF_HUB_ENABLE_HF_TRANSFER=1`)
5. **Apptainer 샌드박스 이미지**: 에이전트가 사용자 코드 실행할 컨테이너 (Python/Node/기본 빌드툴 포함)
6. **소스 복사**: `loopcoder/` 전체
7. **manifest.yaml**: 모든 산출물의 SHA256 + 버전 기록
8. **압축**(옵션): 단일 tarball로 묶기 (`--no-compress` 옵션 지원, 모델이 이미 큰 binary라 압축 효과 작음)

**bundle.sh 인터페이스**:
```
bash bundle.sh                            # 풀 번들
bash bundle.sh --output /export/bundle/   # 출력 위치
bash bundle.sh --skip-model               # 모델 제외 (이미 /models에 있을 때)
bash bundle.sh --skip-container           # 컨테이너 제외
bash bundle.sh --verify                   # 기존 번들의 manifest 검증만
```

### 3.3 전송
- `rsync -avP --partial /export/bundle/ b300:/models/` (재개 가능)
- 또는 외장 디스크 → 서버 마운트 → `cp -a` (대역폭 좋을 때)

### 3.4 `setup.sh` (B300에서 실행, **오프라인**)

- 외부 네트워크 일체 접근 안 함
- `/models/manifest.yaml` 검증 → 모든 파일 SHA256 체크
- 그 후 Phase 1 단계 진행 (오프라인 모드)

### 3.5 검증
- `bundle.sh` 결과를 인터넷 가능 머신에서 한 번 압축해제 → 검증
- B300 서버에서 인터넷 끊은 상태로 setup.sh 통과 확인 (`ip route del default` 후 시뮬레이션)

### 3.6 VM 기반 번들 빌드 (★ 호스트 22.04 → VM 24.04)

#### 3.6.1 왜 VM이 필요한가
- 호스트(이 개발 머신): **Ubuntu 22.04** (jammy)
- 타겟(B300): **Ubuntu 24.04** (noble)
- `apt-get download`은 호스트 OS의 패키지 인덱스를 사용 → 22.04 .deb 받으면 B300에서 의존성 깨짐 (libc, glibc, openssl 등 ABI 차이)
- Python wheel ABI 태그(`cp312-...-manylinux_2_28`)는 OS 비종속이나, **Ubuntu 24.04 기본 Python 3.12** 환경에서 검증해야 안전
- vLLM/Apptainer 컨테이너 내부 GLIBC 의존성 문제 회피
- 따라서 **24.04 VM 안에서 모든 수집을 수행**

#### 3.6.2 VM 사양 (libvirt/KVM, virt-manager)

| 항목 | 값 | 근거 |
|---|---|---|
| OS | Ubuntu 24.04.x LTS (정확한 점버전 고정) | 타겟과 동일 |
| Python | 3.12 (24.04 기본) | 타겟 venv와 동일 |
| vCPU | 4 이상 | 패키지 다운로드 병렬화 |
| RAM | 16 GB 이상 | apt 캐시, hf_transfer |
| 디스크 (시스템) | 50 GB | OS + 캐시 |
| 디스크 (출력) | virtiofs로 호스트 마운트 | 모델 480GB → VM 디스크 폭증 회피 |
| 네트워크 | NAT (인터넷 가능) | 패키지 다운로드 |
| GPU | 미할당 | 추론 안 함, 다운로드만 |

#### 3.6.3 디렉토리 매핑

```
[Host: Ubuntu 22.04]                        [VM: Ubuntu 24.04]
/data/loopcoder-bundle/      ──virtiofs──▶  /output/
  (최종 번들 출력)                              (VM이 여기에 씀)

/data/loopcoder-vm/          (VM 시스템 디스크 위치)
  ├── ubuntu-24.04.qcow2
  └── seed-cloud-init.iso
```

#### 3.6.4 호스트 측 산출물 (`bundle/vm/`)

| 파일 | 책임 |
|---|---|
| `cloud-init/user-data` | 자동 설치: ssh-key, 사용자, 패키지 사전 설치 |
| `cloud-init/meta-data` | hostname, instance-id |
| `domain.xml.template` | libvirt 도메인 정의 (virtiofs 마운트 포함) |
| `setup_vm.sh` | (호스트에서 1회) ISO 다운로드, qcow2 생성, `virt-install`, virtiofs 설정 |
| `start_vm.sh` | VM 부팅 + SSH 대기 |
| `run_in_vm.sh` | 호스트→VM SSH로 `bundle_in_vm.sh` 실행 |
| `destroy_vm.sh` | VM 종료 + 디스크 정리 (옵션) |

#### 3.6.5 VM 측 산출물 (`bundle/in_vm/`)

| 파일 | 책임 |
|---|---|
| `bootstrap.sh` | VM 첫 부팅 후: apt update, 도구 설치, fakeroot 등 |
| `collect_apt.sh` | 24.04용 .deb 다운로드 (`apt-get download` + `apt-rdepends`) |
| `collect_wheels.sh` | Python 휠 (`pip download`) — vLLM, openai SDK, jinja2, rich, pydantic, pytest, GitPython, sqlalchemy, tiktoken 등 |
| `collect_vllm_image.sh` | vLLM Docker 이미지 → `apptainer build` 또는 `apptainer pull docker-daemon://` |
| `collect_sandbox_image.sh` | Sandbox용 가벼운 Python 컨테이너 빌드 |
| `collect_model.sh` | `huggingface-cli download` + 무결성 |
| `make_manifest.sh` | 모든 산출물 SHA256 + 버전 기록 |

#### 3.6.6 호스트 오케스트레이터 `bundle.sh`

논리:
```bash
bundle.sh
├── 0. preflight: virt-manager/libvirt 설치 확인 (없으면 안내)
├── 1. setup_vm.sh (없으면 VM 생성)
├── 2. start_vm.sh (부팅 + SSH 대기)
├── 3. run_in_vm.sh
│   └── ssh vm "cd /output && bash /opt/bundle/in_vm/collect_apt.sh"
│   └── ssh vm "... collect_wheels.sh"
│   └── ssh vm "... collect_vllm_image.sh"
│   └── ssh vm "... collect_sandbox_image.sh"
│   └── ssh vm "... collect_model.sh"
│   └── ssh vm "... make_manifest.sh"
├── 4. verify: 호스트에서 manifest 검증
├── 5. (옵션) destroy_vm.sh
└── 6. summary: 출력 위치, 크기, 다음 단계 안내
```

#### 3.6.7 버전 고정 (Reproducibility)

- Ubuntu 점버전: `etc/lsb-release` 의 `DISTRIB_DESCRIPTION` 기록
- Apptainer 버전: `apptainer --version` 기록
- vLLM 이미지: docker digest (sha256) 고정
- Python 패키지: `requirements.lock` (uv lock 또는 pip freeze) 사용
- 모델 SHA256: 파일별 기록
- 모든 항목 → `manifest.yaml`에 영구 기록

#### 3.6.8 인터페이스

```bash
bash bundle.sh                              # 풀 실행 (VM 생성 → 수집 → 검증)
bash bundle.sh --vm-existing my-vm-24       # 기존 VM 재사용
bash bundle.sh --skip-vm-create             # VM 이미 있을 때
bash bundle.sh --skip-model                 # 모델 제외
bash bundle.sh --skip-container             # 컨테이너 제외
bash bundle.sh --output /data/bundle/v2/    # 출력 위치
bash bundle.sh --verify-only                # 기존 번들 manifest만 검증
bash bundle.sh --destroy-vm                 # 끝나고 VM 정리
```

#### 3.6.9 수락 조건

1. 깨끗한 22.04 호스트(virt-manager 설치)에서 `bundle.sh` 한 번에 통과
2. 24.04 VM 안에서 모든 collect_*.sh 0 종료
3. manifest.yaml의 모든 SHA256이 호스트에서 재검증 OK
4. 다른 가상의 24.04 머신(또는 lxc 컨테이너)에서 wheels로 `pip install --no-index --find-links wheels/ vllm` 성공
5. .deb 의존성 그래프에 누락 없음 (`apt-get install --simulate` 통과)

### 3.7 Setup 자동 검증 VM (★ B300 배포 전 필수)

> **원칙**: B300 서버에 setup.sh를 올리기 전에, 동일 OS(24.04) + 인터넷 차단 환경에서
> setup.sh를 자동으로 돌려 GPU-비의존 단계를 모두 통과시킨다.
> 매번 B300으로 시행착오하지 않는다.

#### 3.7.1 Test VM 사양

| 항목 | 값 | 근거 |
|---|---|---|
| OS | Ubuntu 24.04.x (Bundle VM과 동일 점버전) | B300과 동일 |
| 인터넷 | **차단** (libvirt `<interface type='none'/>` 또는 NAT 후 iptables DROP) | 오프라인 검증 |
| GPU | 없음 (setup.sh가 자동 감지하여 GPU 단계 스킵) | 호스트에 GPU 없거나 패스스루 안 함 |
| vCPU/RAM | 4 / 8GB | setup만 (vLLM 실행 X) |
| 시스템 디스크 | 30 GB qcow2 | OS + apt 설치 |
| 마운트(읽기) | `/data/loopcoder-bundle` → VM의 `/models` (virtiofs ro) | B300의 `/models` 시뮬레이션 |
| 마운트(쓰기) | `/data/loopcoder-test-scratch` → VM의 `/scratch` (virtiofs rw) | B300의 `/scratch` 시뮬레이션 |

#### 3.7.2 setup.sh의 `--skip-gpu-stages` 모드

setup.sh는 다음 환경 변수/플래그를 인식:
- `LOOPCODER_TEST_MODE=1` 또는 `--skip-gpu-stages`
- GPU 없는 환경(`nvidia-smi` 부재)에서 자동 감지하면 경고 후 스킵

스킵되는 stage:
- **S1 hw_check**: GPU 8장 검증 → "TEST MODE: GPU 검증 스킵" 출력 후 통과
- **S10 start_vllm**: systemd unit 등록만 하고 실제 시작은 X (`systemctl is-enabled` 만 확인)
- **S11 smoke_test**: vLLM 호출 대신 "vLLM 컨테이너 import 가능"만 검증
  (`apptainer exec vllm.sif python -c "import vllm"` 통과만 확인)

검증되는 stage (GPU 없이도 OK):
- S0 preflight, S2 manifest_verify, S3 apt_offline, S4 apptainer, S5 python_env,
- S6 agent_deps, S7 model_stage(파일 복사·해시 확인), S8 vllm_image(import만),
- S9 systemd_unit, S12 agent_install, S13 summary

→ **13단계 중 10단계가 Test VM에서 자동 검증 가능**.
나머지 3단계(S1, S10, S11)는 B300에서 1회 수동 검증.

#### 3.7.3 호스트 측 산출물 (`bundle/test_vm/`)

| 파일 | 책임 |
|---|---|
| `cloud-init/user-data` | ssh-key, 사용자, 타임존만 (인터넷 X라 패키지 설치 X) |
| `cloud-init/meta-data` | hostname=loopcoder-test |
| `domain.xml.template` | libvirt 도메인 (인터넷 차단 + virtiofs 2개) |
| `setup_test_vm.sh` | 1회 생성: ISO 다운로드(호스트 인터넷 사용), qcow2 30GB, virt-install |
| `start_test_vm.sh` | 부팅 + SSH 대기 |
| `run_setup_in_vm.sh` | VM 안에서 setup.sh 실행 + stdout/exit code 캡처 |
| `assert_setup_results.sh` | VM 안 상태 검증: `apptainer --version`, `systemctl is-enabled vllm`, `loopcoder --version`, 경로 존재 등 |
| `destroy_test_vm.sh` | 정리 |
| `test_setup.sh` | **호스트 오케스트레이터**: 위 단계를 일괄 실행, 리포트 생성 |

#### 3.7.4 워크플로우

```
bundle.sh                                        (Bundle VM, 인터넷 ON)
   ↓ 결과: /data/loopcoder-bundle/

test_setup.sh                                    (Test VM, 인터넷 OFF)
   ├── (1) Test VM 생성/시작 (없으면)
   ├── (2) /data/loopcoder-bundle → VM /models (virtiofs ro)
   ├── (3) VM 안에서: bash /models/setup.sh --skip-gpu-stages
   ├── (4) VM 안 상태 검증: assert_setup_results.sh
   ├── (5) 결과 리포트 → /data/loopcoder-test-results/<ts>.md
   └── (6) Test VM 종료/유지 (옵션)
```

#### 3.7.5 자동화 통합

```bash
# 권장 워크플로우 (한번에)
bash bundle.sh && bash test_setup.sh

# 또는 합쳐진 한 명령
bash bundle.sh --and-test
```

CI 흐름 (반복 개발 중):
- 코드 변경 → `bundle.sh --skip-model --skip-container` (소스/wheel만 갱신)
- → `test_setup.sh` (수십 초 수준)
- → 통과 시 B300으로 rsync

#### 3.7.6 수락 조건

1. Test VM이 인터넷 차단 상태에서 부팅됨 (`getent hosts huggingface.co` 실패)
2. setup.sh `--skip-gpu-stages` 모드로 stage 0,2~9,12,13 모두 0 종료
3. `apptainer exec /scratch/.../vllm.sif python -c "import vllm"` 통과
4. `systemctl is-enabled vllm` 통과 (시작은 안 함)
5. `loopcoder --version` 정상 출력
6. assert_setup_results.sh 모든 체크 통과
7. 리포트 파일이 사람 읽기 좋게 출력됨

---

## 4. Phase 1 — `setup.sh` 상세 설계

### 4.1 설계 원칙
- **오프라인 우선**: 어떤 단계도 외부망 호출하지 않음. 시도 시 실패로 처리.
- **멱등성**: 몇 번을 다시 돌려도 같은 상태로 수렴
- **단계별 마커**: `/scratch/loopcoder/.stage_<n>` 파일로 진행상황 추적
- **명시적 실패**: 어느 단계가 실패했는지 즉시 출력하고 종료
- **로그 일원화**: 모든 출력 → 콘솔 + `/var/log/loopcoder/setup-<ts>.log`
- **롤백 가능**: `--uninstall` 으로 깔끔히 제거
- **비대화 기본**: `--non-interactive` 시 `install.yaml`에서만 입력

### 4.2 단계 (Stage)

| 단계 | 이름 | 목적 | 검증 |
|---|---|---|---|
| 0 | preflight | 권한·OS·커널·디스크·`/models` 마운트 | sudo + Ubuntu 24.04 + `/models` 존재 |
| 1 | hw_check | GPU/CUDA/PCIe | `nvidia-smi -L` 8장, CUDA ≥ 12.8 |
| 2 | manifest_verify | 번들 무결성 | manifest.yaml의 모든 SHA256 일치 |
| 3 | apt_offline | 오프라인 .deb 설치 | `dpkg -l` 확인 |
| 4 | apptainer | Apptainer 설치 (apt 오프라인 또는 .sif 단일파일) | `apptainer --version` |
| 5 | python_env | Python 3.12 + uv + venv (오프라인 wheelhouse) | `python --version` |
| 6 | agent_deps | loopcoder 의존성 설치 (오프라인 wheels) | `import loopcoder` 성공 |
| 7 | model_stage | `/models/<model>` → `/scratch/models/<model>` 배치 | config.json 로드 + 파일 수 일치 |
| 8 | vllm_image | vLLM .sif → `/scratch/loopcoder/containers/` 배치 | `apptainer exec ... python -c "import vllm"` |
| 9 | systemd_unit | `vllm.service` 등록 | `systemctl is-enabled vllm` |
| 10 | start_vllm | 서비스 시작 (Apptainer로 wrap) | `/v1/models` 200 응답 (최대 15분 대기) |
| 11 | smoke_test | "1+1=" → "2" 포함 응답 | 응답 정상 |
| 12 | agent_install | `loopcoder` CLI 등록 | `loopcoder --version` |
| 13 | summary | 사용법 출력 | - |

### 4.3 systemd unit (Apptainer 래핑, 요지)

```ini
[Unit]
Description=vLLM Inference Server (Apptainer)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=loopcoder
EnvironmentFile=/etc/loopcoder/vllm.env
ExecStartPre=/bin/bash -c 'test -f /scratch/models/${MODEL_ID_DIR}/config.json'
ExecStart=/usr/bin/apptainer run --nv \
  --bind /scratch/models:/models:ro \
  --bind /scratch/loopcoder/cache:/cache \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  /scratch/loopcoder/containers/vllm.sif \
  /opt/vllm/bin/vllm serve /models/${MODEL_ID_DIR} \
    --tensor-parallel-size ${TENSOR_PARALLEL_SIZE} \
    --max-model-len ${MAX_MODEL_LEN} \
    --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION} \
    --quantization ${QUANTIZATION} \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --max-num-seqs ${MAX_NUM_SEQS} \
    --host ${HOST} --port ${PORT}
Restart=on-failure
RestartSec=15
LimitNOFILE=1048576
LimitMEMLOCK=infinity

[Install]
WantedBy=multi-user.target
```

### 4.4 setup.sh 인터페이스

```
sudo bash setup.sh                          # 풀 설치 (install.yaml 사용)
sudo bash setup.sh --config /path/to.yaml   # 다른 설정 파일
sudo bash setup.sh --bundle /models         # 번들 위치 (디폴트 /models)
sudo bash setup.sh --dry-run                # 무엇을 할지만 출력
sudo bash setup.sh --reinstall              # 강제 재설치
sudo bash setup.sh --skip-model-stage       # 모델 복사 스킵 (이미 /scratch에)
sudo bash setup.sh --uninstall              # 깨끗한 제거
sudo bash setup.sh --stage 7                # 특정 단계부터 재시작
```

### 4.5 setup.sh 수락 조건

1. 깨끗한 Ubuntu 24.04 + 드라이버만 있는 노드(인터넷 끊긴 상태)에서 한 번 실행 → Stage 0~13 모두 OK
2. 중간에 강제 종료(Ctrl-C) 후 재실행 → 마지막 성공 단계 다음부터 재개
3. `curl http://127.0.0.1:8000/v1/models` 가 모델 목록 반환
4. `curl http://127.0.0.1:8000/v1/chat/completions` 으로 "1+1=" 보내면 "2" 포함 응답
5. 노드 재부팅 후 `systemctl status vllm` 이 active(running) 상태
6. 외부 DNS 차단 상태에서도 모든 단계 통과 (`getent hosts huggingface.co` 실패해도 OK)

---

## 5. Phase 2 — `loopcoder` 에이전트 상세 설계

### 5.1 디렉토리 구조 (확정)

```
/scratch/loopcoder/agent/    (소스 위치, 시스템에는 /opt/loopcoder 심볼릭링크)
├── pyproject.toml
├── README.md
├── loopcoder/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py             # YAML 다중 파일 로딩 + 머지
│   ├── llm/
│   │   ├── client.py         # OpenAI SDK 래퍼 + 재시도/백오프
│   │   ├── prompts.py        # jinja2 템플릿
│   │   ├── tokens.py         # 토큰 카운팅
│   │   └── context.py        # 컨텍스트 윈도 관리 (5.7 참조)
│   ├── plan/
│   │   ├── schema.py         # Pydantic
│   │   ├── parser.py         # YAML/Markdown 입력
│   │   └── topo.py           # depends_on 정렬
│   ├── tools/
│   │   ├── base.py           # Tool ABC, JSON schema 자동 생성
│   │   ├── registry.py
│   │   ├── fs.py             # read/write/edit/list/grep/find
│   │   ├── shell.py          # run_shell (allowlist+timeout)
│   │   ├── git.py            # snapshot/diff/revert
│   │   └── tests.py          # pytest/jest/cargo/go 자동 감지
│   ├── sandbox/
│   │   ├── base.py           # Sandbox ABC
│   │   ├── apptainer.py      # ★ 기본 백엔드
│   │   └── host.py           # --no-sandbox (옵트인, 위험)
│   ├── loop/
│   │   ├── controller.py     # 메인 PDCA 루프
│   │   ├── verifier.py       # acceptance 실행/판정
│   │   ├── critic.py         # 자기비평 (선택)
│   │   └── strategy.py       # 연속 실패 시 전략 변경
│   ├── state/
│   │   ├── store.py          # SQLite
│   │   ├── snapshot.py       # git tag 기반 체크포인트
│   │   └── replay.py
│   └── ui/
│       ├── tty.py            # rich 라이브 진행상황
│       └── report.py         # Markdown 리포트
├── tests/
│   ├── unit/...
│   ├── integration/...
│   └── fixtures/...
└── examples/
    ├── plan_simple.yaml
    ├── plan_fastapi.yaml
    └── workspaces/
```

### 5.2 plan.yaml 스키마 (확정)

```yaml
# 최상위
project:
  name: string                  # 필수
  workspace: path               # 필수, 절대경로 (host 또는 /workspace 매핑)
  language: enum?               # python|node|rust|go|... 자동감지

constraints:
  max_iterations_per_goal: int  # 0=무한, 기본 50
  max_total_minutes: int        # 0=무한, 기본 360
  max_tokens_per_iter: int      # 기본 200000
  forbidden_paths: [path]       # glob
  allowed_shell_commands: [string]  # glob 패턴
  network_allowed: bool         # 기본 false (Apptainer --net 없음)

context:
  description: string
  files_to_read_first: [path]   # 시작 시 LLM이 읽는 파일
  reference_docs: [path|url]    # 참조 문서 (오프라인이라 path 위주)
  pin_in_context:               # ★ 항상 컨텍스트에 유지할 파일 (5.7)
    - path: README.md
    - path: docs/architecture.md

goals:
  - id: string                  # 필수, 고유
    title: string
    description: string
    depends_on: [goal_id]
    priority: int               # 기본 100
    acceptance:                 # 필수, 1개 이상
      - kind: shell
        run: string
        cwd: path?
        timeout: int            # 기본 300초
        expect:
          exit_code: int        # 기본 0
          stdout_contains: string?
          stderr_not_contains: string?
      - kind: file_exists
        path: path
      - kind: file_contains
        path: path
        pattern: regex
      - kind: file_not_contains
        path: path
        pattern: regex
      - kind: http
        prepare: string?
        request:
          method: string
          url: string
          headers: dict?
          body: any?
        expect:
          status: int
          body_contains: string?

llm:                            # plan별 오버라이드 (옵션)
  model: string?
  temperature: float            # 기본 0.2
  top_p: float                  # 기본 0.95
  max_completion_tokens: int    # 기본 8192
```

### 5.3 도구 시스템 (확정)

| Tool | 책임 | 안전장치 |
|---|---|---|
| `read_file` | 파일 일부/전체 | forbidden_paths, 크기 한도 |
| `read_files` | 여러 파일 한번에 (배치, 토큰 절약) | 합산 크기 한도 |
| `list_dir` | 디렉토리 목록 | 깊이/결과수 한도 |
| `grep` | 정규식 검색 | 매칭수 한도 |
| `find_files` | glob 검색 | 결과수 한도 |
| `write_file` | 새 파일 생성 | forbidden_paths, 자동 git add |
| `edit_file` | old→new 교체 (unique) | 다중 매칭 거부 |
| `apply_patch` | unified diff | 충돌 감지 |
| `run_shell` | 명령 실행 | allowlist+timeout+출력 cap |
| `run_tests` | 테스트 자동 감지·실행 | 결과 구조화 |
| `git_status/diff/log` | git 상태 | - |
| `revert_to_snapshot` | 골 시작점으로 롤백 | 명시적 호출만 |
| `record_thought` | 자유 메모 | 검증 X, 디버깅용 |
| `submit_goal` | "끝났음" 선언 | 검증은 verifier가 |

### 5.4 메인 루프 의사코드

```python
def run(plan):
    sandbox.prepare(plan.project.workspace)   # apptainer instance start
    git.init_if_needed()
    git.snapshot(tag="initial")

    for goal in topological_order(plan.goals):
        with session.start(goal):
            iteration = 0
            consecutive_failures = 0
            while not goal.verified:
                iteration += 1
                if exceeded_limits(goal, iteration):
                    goal.status = "failed"
                    break

                ctx = context.build(           # ★ 컨텍스트 전략 (5.7)
                    goal=goal,
                    pinned=plan.context.pin_in_context,
                    file_tree=sandbox.tree(),
                    git_diff_since_goal_start=git.diff(),
                    recent_attempts=session.recent(n=10),
                    recent_verify_logs=session.recent_verify(n=3),
                )

                response = llm.chat_with_tools(
                    messages=ctx,
                    tools=registry.all(),
                    parallel_tool_calls=True,
                )

                for tool_call in response.tool_calls:
                    result = sandbox.execute(tool_call)
                    session.record(tool_call, result)

                # 검증은 항상 LLM 외부에서
                verify = verifier.run(goal.acceptance)
                session.record_verification(verify)

                if verify.passed:
                    if config.use_critic:
                        critic_ok = critic.review(goal, git.diff(), verify)
                        if not critic_ok:
                            continue
                    git.snapshot(tag=goal.id)
                    goal.verified = True
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= config.strategy_change_after:
                        strategy.intervene(goal, session)
                    if consecutive_failures >= config.rollback_after:
                        git.revert_to(last_good_snapshot)
                        consecutive_failures = 0

    report.generate()
```

### 5.5 프롬프트 전략

- **System**: 역할/제약/도구 사용 규칙 (jinja2)
- **Goal**: 현재 골 + acceptance 명시 (LLM이 뚫어야 할 조건 인지)
- **Failure feedback**: 실패한 acceptance의 stdout/stderr **그대로** (요약 없이)
- **Strategy change**: 연속 실패시 "지금까지 접근을 비판하고 다른 길 제안" 주입

### 5.6 안전장치

1. **검증 외부화**: acceptance는 LLM 외부에서 실행
2. **submit_goal ≠ 검증 통과**: LLM 자기보고 무시
3. **Git 강제**: 모든 파일변경 후 자동 commit, diff 추적
4. **롤백**: 골 성공시 태그, 실패 누적시 자동 revert
5. **자원 한도**: iter/시간/토큰/디스크 모두 상한
6. **금지 경로**: `.env`, `secrets/` + 사용자 지정 → 모든 도구에서 검사
7. **셸 allowlist**: 패턴 매칭. 기본 `pytest*`, `python*`, `pip*`, `ls`, `cat`, `npm test*` 등
8. **네트워크 격리**: Apptainer 인스턴스 시작시 `--net --network=none`(또는 fakeroot 모드)으로 외부망 차단
9. **로그 영속화**: SQLite에 모든 메시지·도구호출·결과·검증 결과 저장
10. **읽기 한도**: 한 번에 읽을 수 있는 파일 크기/grep 결과 수 제한
11. **Apptainer 격리**: `--containall --no-home --readonly` + 명시적 bind만 허용
12. **plan.yaml 보호**: 골 시작 전 해시 기록, LLM이 plan을 수정하면 거부

### 5.7 컨텍스트 관리 전략 ★ (1M 컨텍스트 활용)

> 사용자 요청: "컨텍스트 중요하게 봐야할 것 같아"
> 모델이 256K~1M까지 활용 가능 → **요약·드롭을 최소화**하고 **선별적 보존**.

#### 5.7.1 컨텍스트 윈도 구조 (우선순위 순)

| 순위 | 내용 | 정책 |
|---|---|---|
| 1 | System 프롬프트 (도구 정의 포함) | 항상 |
| 2 | 현재 goal + acceptance | 항상 (변동 없음) |
| 3 | `pin_in_context`로 지정한 파일 | 항상, 풀 텍스트 |
| 4 | **최근 verify 실패 로그 N개** (전체) | 항상 풀텍스트, 잘림 X |
| 5 | 골 시작 이후 git diff | 항상, 너무 크면 파일별 분할 |
| 6 | 최근 도구호출/결과 (recent_attempts) | 토큰 여유 만큼 |
| 7 | 파일트리 요약 (현재 워크스페이스) | 자동 생성 |
| 8 | 과거 시도(이전 goal/iter) | 압축 요약본만 |

#### 5.7.2 핵심 원칙
- **검증 로그 절대 요약 금지**: 디버깅의 핵심 정보. 잘리면 LLM이 헛것을 봄.
- **diff 우선 > 파일 풀텍스트**: 같은 파일을 매번 풀로 보내지 않고 변화분만
- **고정 파일은 prefix caching**: vLLM의 prefix cache 활용 (시스템·pinned 파일은 캐시 hit)
- **점진적 요약**: 컨텍스트 70% 도달 시 가장 오래된 시도부터 요약본으로 대체 (verify 로그는 예외)
- **읽기 결과 캐시**: 같은 파일을 두 번 읽지 않게 컨텍스트 내 중복 제거
- **컨텍스트 사용량 가시화**: 매 iter마다 token usage breakdown 로그

#### 5.7.3 구현 위치
- `loopcoder/llm/context.py`: ContextBuilder 클래스
- `loopcoder/llm/tokens.py`: tiktoken/transformers tokenizer 기반 카운팅
- `loopcoder/state/store.py`: 과거 시도의 압축본 저장

---

## 6. 설정 파일 (YAML 다중) ★

> 사용자 요청: "옵션 줘야 하는 것들은 yaml로 설정 파일 만들도록"

### 6.1 파일 구성

| 파일 | 책임 | 변경 빈도 |
|---|---|---|
| `/etc/loopcoder/install.yaml` | 설치 시 1회 결정 (경로, 모델, 컨테이너) | 거의 없음 |
| `/etc/loopcoder/vllm.yaml` | vLLM 런타임 (TP, 컨텍스트 길이, 양자화) | 가끔 |
| `/etc/loopcoder/loopcoder.yaml` | 에이전트 동작 (루프, 샌드박스, 컨텍스트) | 자주 |
| `<plan>/plan.yaml` | 사용자 골/제약 (프로젝트별) | 매번 |

### 6.2 `install.yaml` 예시

```yaml
deployment:
  mode: offline                       # offline | online
  bundle_root: /models                # 번들 위치 (ro)

paths:
  install_root: /scratch/loopcoder
  model_cache: /scratch/models
  workspaces_root: /scratch/workspaces
  log_dir: /var/log/loopcoder
  state_dir: /var/lib/loopcoder

model:
  id: "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"
  source_path: /models/Qwen3-Coder-480B-A35B-Instruct-FP8
  destination_path: /scratch/models/Qwen3-Coder-480B-A35B-Instruct-FP8
  staging:
    strategy: rsync                   # rsync | symlink | copy
    verify_sha256: true

container:
  vllm_image: /models/containers/vllm-0.7.x.sif
  sandbox_image: /models/containers/loopcoder-sandbox.sif

system:
  user: loopcoder
  group: loopcoder
  create_user: true
```

### 6.3 `vllm.yaml` 예시

```yaml
serving:
  host: 127.0.0.1
  port: 8000
  api_key: ""                         # 빈 값=인증 없음 (로컬)

engine:
  tensor_parallel_size: 8
  max_model_len: 262144               # 256K
  max_num_seqs: 8
  gpu_memory_utilization: 0.92
  quantization: fp8
  enable_prefix_caching: true
  enable_chunked_prefill: true
  swap_space_gb: 0
  kv_cache_dtype: auto
  trust_remote_code: false

env:
  HF_HUB_OFFLINE: "1"
  TRANSFORMERS_OFFLINE: "1"
  NCCL_P2P_LEVEL: "NVL"
  VLLM_USE_V1: "1"

systemd:
  restart: on-failure
  restart_sec: 15
```

### 6.4 `loopcoder.yaml` 예시

```yaml
llm:
  base_url: http://127.0.0.1:8000/v1
  api_key: EMPTY
  model: Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8
  temperature: 0.2
  top_p: 0.95
  max_completion_tokens: 8192
  request_timeout_sec: 600
  retry:
    max_attempts: 5
    backoff_initial_sec: 2
    backoff_max_sec: 60

context:                              # 5.7 참조
  total_budget_tokens: 240000         # 256K 중 안전마진
  reserve_for_completion: 8192
  always_pin:
    - README.md
    - docs/**/*.md
  preserve_full:
    verify_logs: true
    git_diff: true
  summarize_oldest_when_over_pct: 70
  per_file_max_kb: 256
  grep_max_results: 200
  list_dir_max_depth: 3
  list_dir_max_entries: 500

sandbox:
  backend: apptainer
  image: /scratch/loopcoder/containers/loopcoder-sandbox.sif
  bind_mounts:
    - source: "{workspace}"
      dest: /workspace
      mode: rw
    - source: /scratch/loopcoder/cache
      dest: /cache
      mode: rw
  network: false
  read_only_paths:
    - /etc
    - /usr
  default_cwd: /workspace
  exec_timeout_sec: 600

tools:
  shell:
    allowed_patterns:
      - "pytest*"
      - "python*"
      - "python3*"
      - "pip*"
      - "uv*"
      - "ls*"
      - "cat *"
      - "head *"
      - "tail *"
      - "grep *"
      - "find *"
      - "git *"
      - "npm test*"
      - "cargo test*"
      - "go test*"
    output_max_kb: 256
    timeout_sec_default: 300
  fs:
    forbidden_paths:
      - "**/.env"
      - "**/secrets/**"
      - "**/.ssh/**"
      - "/etc/**"
    max_read_bytes: 1048576

loop:
  max_iterations_per_goal: 50         # 0=무한
  max_total_minutes: 360              # 0=무한
  strategy_change_after: 3            # 연속 실패 N회 → 전략 변경 프롬프트
  rollback_after: 6                   # 연속 실패 N회 → git revert
  use_critic: false
  parallel_goals: false               # 단일 워커

storage:
  state_db: /var/lib/loopcoder/sessions.db
  log_dir: /var/log/loopcoder
  workspaces_root: /scratch/workspaces

ui:
  tty: rich                           # rich | plain
  log_level: INFO
```

### 6.5 설정 우선순위

```
plan.yaml (project-specific)
  > CLI flag
    > /etc/loopcoder/loopcoder.yaml
      > /etc/loopcoder/install.yaml (불변)
        > 코드 디폴트
```

### 6.6 검증
- `loopcoder config validate` 명령 → Pydantic 모델로 모든 yaml 검증
- 잘못된 키/타입 → 즉시 에러
- 환경변수 치환 지원 (`${VAR}`)

---

## 7. CLI (확정)

```
loopcoder init                                    # 설치 후 설정 초기화 마법사 (옵션)
loopcoder config validate [--file ...]            # YAML 검증
loopcoder config show                             # 현재 머지된 설정 출력
loopcoder run --plan plan.yaml                    # 실행
loopcoder run --plan plan.yaml --goal g1          # 특정 goal만
loopcoder run --plan plan.yaml --resume           # 마지막 세션 이어서
loopcoder list                                    # 세션 목록
loopcoder status [session_id]                     # 진행상황
loopcoder logs <session_id> [--follow]            # 로그 보기
loopcoder report <session_id> > report.md         # 최종 보고서
loopcoder replay <session_id> --to-iter 5         # 시점까지 리플레이
loopcoder rollback <session_id> --to-goal g2      # git revert + DB 마킹
loopcoder tokens <session_id>                     # 토큰 사용량 통계
loopcoder --version
```

---

## 8. Phase 3 — 검증 계획

### 8.1 단위 테스트 (pytest)
- 도구별 mock 테스트
- plan 스키마: 잘못된 plan 거부
- Apptainer 샌드박스: forbidden_paths 차단, network=none 확인
- Verifier: 각 acceptance kind별 통과/실패
- Context builder: 토큰 한도 내에서 우선순위 보존 검증
- 설정 머지: 우선순위 정확히 적용
- 상태 저장: CRUD + 동시성

### 8.2 통합 테스트 (실제 vLLM 사용)

| ID | 시나리오 | 시작 상태 | 성공 조건 |
|---|---|---|---|
| E2E-1 | 알려진 버그 수정 | pytest 1개 실패 | 모든 pytest 통과 |
| E2E-2 | 신규 기능 (FastAPI) | 빈 디렉토리 | `/hello` 200 + 테스트 통과 |
| E2E-3 | 리팩토링 | 큰 함수 + 테스트 | 분리 + 테스트 통과 |
| E2E-4 | 무한루프 방지 | 풀 수 없는 acceptance | max_iterations 도달 후 종료 |
| E2E-5 | 거짓 보고 검출 | LLM이 "다 됐다"고 했지만 acceptance 실패 | 시스템이 거부, 다음 iter |
| E2E-6 | 롤백 | 실패 누적 | 자동 revert |
| E2E-7 | 컨텍스트 보존 | 큰 verify 로그 발생 | 로그가 잘리지 않고 다음 iter에 전달 |
| E2E-8 | 오프라인 | 외부망 차단 | 모든 단계 정상 |

### 8.3 성능/안정성 측정
- vLLM 단독 처리량(`vllm bench`): 입력 4K/16K/64K, 동시성 1·4·8
- 에이전트 1 골당 평균 시간/토큰
- 24시간 연속 실행시 메모리 누수
- 비정상 종료 후 재개 정상 동작

### 8.4 수락 게이트
- [ ] bundle.sh 깨끗한 머신에서 통과 + manifest 검증
- [ ] setup.sh 오프라인 노드에서 1회 통과
- [ ] setup.sh 중단 후 재개 정상
- [ ] vLLM `/v1/models` 응답
- [ ] 단위 테스트 100% 통과
- [ ] E2E-1~8 모두 통과
- [ ] INSTALL.md대로 따라하면 빈 노드에서 동작
- [ ] `loopcoder config validate` 통과
- [ ] `loopcoder --help` 모든 서브커맨드 동작

---

## 9. 산출물

```
loopcoder/                          # 프로젝트 루트
├── PLAN.md                       # 본 문서
├── PROGRESS.md                   # 실시간 구현현황 (★ 매 작업마다 갱신)
├── INSTALL.md                    # 설치 가이드
├── README.md                     # 개요
├── bundle.sh                     # 호스트 오케스트레이터 (22.04, virt-manager 사용)
├── setup.sh                      # B300용 (오프라인)
├── test_setup.sh                 # Test VM 자동 검증 오케스트레이터
├── bundle/
│   ├── vm/                       # 호스트 측 Bundle VM (24.04, 인터넷 ON)
│   │   ├── cloud-init/{user-data, meta-data}
│   │   ├── domain.xml.template
│   │   ├── setup_vm.sh / start_vm.sh / run_in_vm.sh / destroy_vm.sh
│   ├── in_vm/                    # VM 안에서 실행될 수집 스크립트
│   │   ├── bootstrap.sh / collect_apt.sh / collect_wheels.sh
│   │   ├── collect_vllm_image.sh / collect_sandbox_image.sh
│   │   ├── collect_model.sh / make_manifest.sh
│   └── test_vm/                  # Test VM (24.04, 인터넷 OFF, GPU 없음)
│       ├── cloud-init/{user-data, meta-data}
│       ├── domain.xml.template   # net=none + virtiofs 2개
│       ├── setup_test_vm.sh / start_test_vm.sh
│       ├── run_setup_in_vm.sh    # VM 안에서 setup.sh --skip-gpu-stages
│       ├── assert_setup_results.sh
│       └── destroy_test_vm.sh
├── scripts/
│   ├── healthcheck.sh
│   ├── benchmark.sh
│   ├── uninstall.sh
│   └── make_apptainer_images.sh  # vLLM/sandbox .sif 빌드 (단독실행용)
├── systemd/
│   └── vllm.service.template
├── config/
│   ├── install.yaml.example
│   ├── vllm.yaml.example
│   └── loopcoder.yaml.example
├── containers/                   # Apptainer 빌드 정의
│   ├── vllm.def
│   └── loopcoder-sandbox.def
├── agent/                        # 5.1 구조
└── examples/
    ├── plan_simple.yaml
    ├── plan_fastapi.yaml
    └── workspaces/
```

---

## 10. 위험과 대응 (Risk Register)

| ID | 위험 | 영향 | 대응 |
|---|---|---|---|
| R1 | vLLM 0.7+ Blackwell 지원 미흡 | 추론 자체 불가 | 백업: TensorRT-LLM/SGLang 시도, 최악의 경우 BF16 폴백 |
| R2 | 모델 게이트화로 다운로드 불가 | 셋업 중단 | 게이트 없는 대안(Qwen3-Coder, DeepSeek-V3 비게이트) 자동 선택 |
| R3 | LLM이 acceptance 자체 수정 시도 | 검증 무력화 | plan.yaml 해시 검증, 샌드박스 ro 마운트 |
| R4 | 무한 토큰 소비 | 시간 폭증 | iter/토큰/시간 한도 + 강제 중단 |
| R5 | Apptainer 탈출 (실수로 호스트 변경) | 호스트 손상 | `--containall --readonly` + 명시 bind만, 호스트모드는 옵트인 |
| R6 | 동시 다중 골 race | 상태 깨짐 | Phase 2: 단일 워커. 멀티는 차후 |
| R7 | 대용량 출력 컨텍스트 폭발 | 비용/실패 | 출력 크기 cap + tail 자동 |
| R8 | Critic이 정상 결과 거부 | 진행 멈춤 | Critic은 옵션, 기본 비활성 |
| R9 | systemd vllm 죽음 | 운영 중단 | restart=on-failure + healthcheck cron |
| R10 | 모델 변경 시 프롬프트 호환성 | 품질 저하 | 모델별 어댑터 |
| R11 | `/models`가 NFS라 부팅시 미마운트 | vllm 시작 실패 | systemd `RequiresMountsFor=/models` |
| R12 | 번들 전송 중 손상 | manifest 검증 실패 | rsync --partial 재개, manifest SHA256 |
| R13 | `/scratch` 디스크 부족 | 설치 실패 | preflight에서 1TB+ 검증, 부족시 명확 에러 |
| R14 | Apptainer 버전 호환성 | 컨테이너 실행 실패 | 번들에 정확한 버전 명시, setup.sh 검증 |
| R15 | 큰 컨텍스트로 vLLM OOM | 추론 실패 | max_model_len 가드, KV cache 모니터링 |

---

## 11. 진행 순서 (Implementation Order)

| Step | 산출 | 검증 방법 |
|---|---|---|
| S1 | install/vllm/loopcoder 설정 스키마 + Pydantic | unit test |
| S1a | **VM 24.04 프로비저닝** (cloud-init, virt-install, virtiofs) | `ssh vm "lsb_release -a"` → 24.04 확인 |
| S1b | VM 안 bootstrap.sh + 호스트 오케스트레이터 골격 | host에서 ssh 명령 0 종료 |
| S2 | collect_apt.sh + collect_wheels.sh (VM 안) | wheels/apt 디렉토리 정상 생성 |
| S3 | collect_vllm_image.sh (vLLM Apptainer .sif) | `apptainer exec vllm.sif python -c "import vllm"` |
| S4 | collect_model.sh + manifest.yaml | manifest SHA256 재검증 통과 |
| S5 | setup.sh Stage 0~6 (인프라, Apptainer, venv, 의존성) | 에이전트 import OK |
| S6 | setup.sh Stage 7~11 (모델 staging, vLLM 시작, smoke) | `/v1/chat/completions` OK |
| S7 | setup.sh Stage 12~13 (에이전트 등록, 사용법) | `loopcoder --version` |
| S8 | plan 스키마 + 파서 + 단위테스트 | pytest 통과 |
| S9 | 도구 시스템 (fs/shell/git/tests) + 단위테스트 | pytest 통과 |
| S10 | Apptainer 샌드박스 + 단위테스트 | 격리 검증 |
| S11 | LLM 클라이언트 + 컨텍스트 빌더 + 토큰관리 | 컨텍스트 우선순위 검증 |
| S12 | Verifier + 메인 루프 | 단순 plan 작동 |
| S13 | Critic + Strategy change | 옵션 동작 |
| S14 | 상태 저장 + 리플레이 + 리포트 | E2E 흐름 |
| S15 | CLI 통합 + UI(rich) | 사람 친화 |
| S16 | E2E 1~8 작성 + 통과 | 전부 green |
| S17 | INSTALL.md, README, 데모 plan | 따라하면 동작 |
| S18 | 수락 게이트 통과 | 8.4 체크리스트 |

> 각 단계는 **이전 단계가 실제 동작 검증된 뒤에만** 다음으로 진행.

---

## 12-α. Claude Code 노하우 통합 (rev.6)

> 최근 Anthropic Claude Code의 시스템 프롬프트·도구 시그니처·운영 패턴이 부분 공개되어
> 우리 LoopCoder 설계에 *패턴*만 차용한다. 문구를 그대로 복붙하지 않고 인터페이스/원칙만 채용.

### 12-α.1 통합할 14개 패턴

| ID | 패턴 | 통합 방식 |
|---|---|---|
| CC1 | Edit unique-match + replace_all | 이미 구현 ✅ |
| CC2 | Read 라인번호 prefix (`cat -n` 포맷) + offset/limit | `tools/fs.py` ReadFileTool 강화 |
| CC3 | Write 덮어쓰기 시 사전 Read 강제 | ToolContext에 `read_files` set, Write에서 검사 |
| CC4 | system-reminder 동적 주입 | `loop/reminders.py` 신규 + controller에서 매 iter prepend |
| CC5 | TodoWrite 내부 작업 추적 도구 | `tools/todo.py` 신규 + state DB에 todos 테이블 |
| CC6 | PreToolUse / PostToolUse 훅 | `tools/hooks.py` 신규 + registry 통합 |
| CC7 | Sub-agent (Task) 도구 | `tools/spawn_agent.py` — 별도 LLM 세션, 결과만 반환 |
| CC8 | System prompt 스타일 압축 (직접·명령형·"do/do not") | `llm/prompts.py` 리라이트 |
| CC9 | Goal-driven loop | 이미 핵심 설계 ✅ |
| CC10 | "Don't add unrequested features" 원칙 | system prompt에 명시 |
| CC11 | Output cap + tail-bias | 이미 `tools/shell.py` 구현 ✅ |
| CC12 | Bash 백그라운드 + 모니터링 | `tools/shell.py`에 `run_shell_background` + `read_background_output` |
| CC13 | Plan mode | plan.yaml이 그 역할 ✅ |
| CC14 | 프로젝트 컨벤션 자동 로드 (`CLAUDE.md`/`AGENTS.md`/`.loopcoderrc`) | controller에서 자동 pin |

### 12-α.2 새 컴포넌트

```
loopcoder/
├── tools/
│   ├── todo.py              ★ TodoWrite 도구
│   ├── spawn_agent.py       ★ Sub-agent 도구
│   ├── hooks.py             ★ Pre/Post tool-use 훅
│   └── (기존 fs.py / shell.py 강화)
├── loop/
│   ├── reminders.py         ★ system-reminder 동적 빌더
│   └── conventions.py       ★ CLAUDE.md/AGENTS.md/.loopcoderrc 자동 로드
└── state/
    └── (store.py에 todos 테이블 추가)
```

### 12-α.3 system-reminder 정책

매 iter LLM 호출 직전에 `system` role로 다음을 주입 (요약 X, 항상 풀텍스트):
1. 현재 진행 중 goal id + acceptance 항목 수
2. 최근 verify 실패 횟수 (있으면)
3. 컨텍스트 사용량 (현재/예산 토큰)
4. 다음 규칙 상기:
   - "검증은 외부에서 실행됨. submit_goal만으로는 끝나지 않음"
   - "이미 읽은 파일은 다시 read_file하지 말 것 (컨텍스트 절약)"
   - "수정 전 반드시 해당 파일을 read 한 적이 있어야 함"
   - "verify 로그는 잘리지 않으니 끝까지 읽을 것"

### 12-α.4 훅 시스템 (CC6)

```python
class HookRegistry:
    pre_tool: dict[str, list[Callable]]    # tool_name → callables
    post_tool: dict[str, list[Callable]]
```

기본 훅:
- **PreToolUse(write_file/edit_file)**: 해당 path가 `ctx.read_files` set에 없으면 `ToolError("must Read before Write/Edit")` (CC3)
- **PostToolUse(read_file)**: ctx.read_files에 path 추가
- **PostToolUse(write_file/edit_file/apply_patch)**: 자동 `git add <path>`, 성공 시 ctx에 변경 카운터 +1
- **PostToolUse(run_shell)**: 출력 → 항상 SQLite store 기록
- **PostToolUse(run_tests)**: 테스트 결과 카운트 → state에 영속

### 12-α.5 Sub-agent (CC7)

`spawn_agent` 도구는:
- 별도 LLM 세션 (자체 system prompt + 제한된 도구 셋)
- 메인 컨텍스트에는 *결과 요약*만 반환 (전체 trace는 SQLite에 보관)
- 용도: 큰 코드베이스 탐색 ("X가 어디서 정의되는지 찾아라"), 복잡한 리팩토링 분석
- 안전: 메인이 정한 forbidden_paths/allowlist 그대로 상속

### 12-α.6 TodoWrite (CC5)

LLM 내부 작업분해 도구. PROGRESS.md와 다른 점:
- PROGRESS.md = **사용자 가시 영속 문서** (프로젝트 전체)
- TodoWrite = **현재 goal 한정의 LLM 자기관리 도구** (세션 종료 시 리포트)

상태: `pending | in_progress | completed | cancelled`. 한 번에 단 하나만 `in_progress`.

---

## 12. 미결 (Open Questions)

> 모든 핵심 의사결정 완료. 구현 진입 가능.
> 추가 결정이 필요해지면 본 절을 갱신하고 PROGRESS.md에 동기화.

---

## 13. 변경 이력

| 일시 | 변경 | 작성자 |
|---|---|---|
| 2026-05-05 | 초안 | Claude |
| 2026-05-05 | rev.2 — Apptainer 채택, 오프라인 번들 워크플로우 추가, YAML 설정 파일 3종, 컨텍스트 관리 전략 명시, /models→/scratch 경로 정책 | Claude |
| 2026-05-05 | rev.3 — §0 철칙 추가(실시간 PROGRESS.md 갱신), PROGRESS.md 신규 | Claude |
| 2026-05-05 | rev.4 — 프로젝트명 LoopCoder/loopcoder 확정, §3.6 VM 기반 번들 빌드 추가 (호스트 22.04 → VM 24.04), D15~D19 결정값 반영 | Claude |
| 2026-05-05 | rev.5 — §3.7 Setup 검증 VM(Test VM) 추가, setup.sh `--skip-gpu-stages` 모드 명시, D20~D23 (테스트 VM, 디스크 경로 `/data/loopcoder-vm/` 등) 반영, 산출물에 `test_setup.sh` + `bundle/test_vm/` 포함 | Claude |
