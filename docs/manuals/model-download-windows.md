# Qwen3-Coder-480B-FP8 — Windows에서 미리 받기 매뉴얼

> **대상 사용자**: Linux 빌드 호스트(Ubuntu 22.04)에 모델을 받아두기 전,
> Windows 머신(개인 PC, 게이밍 데스크톱 등)에서 480GB 모델을 미리 다운로드하려는 사용자.
> **소요 시간**: 회선 속도에 따라 다름. 1Gbps 회선 ≈ 2~4시간, hf_transfer 사용 시 더 빠름.
> **결과**: 그대로 Linux로 옮겨 LoopCoder 번들에 끼워 넣을 수 있는 모델 디렉토리.

---

## 0. 왜 Windows에서?

- **회선이 좋은 데스크톱**이 따로 있어 빠르게 받고 싶을 때
- **Linux 빌드 호스트의 디스크가 작아** 외부에서 받아 외장 SSD로 옮기는 게 편할 때
- **Bundle VM 만들기 전** 미리 모델만 확보해 두고 싶을 때 (병렬 작업)

모델 가중치는 그냥 일반 파일들(JSON + safetensors 바이너리)이라 OS와 무관합니다.
Windows에서 받아도 **byte-for-byte 동일**한 결과를 Linux에 그대로 가져갈 수 있습니다.

---

## 1. 사전 준비

### 1.1 디스크 공간

- **최소 600GB 여유** (FP8 본체 ~480GB + 다운로드 임시 + 안전 마진)
- **NTFS 또는 exFAT 권장**. **FAT32 절대 금지** — safetensors shard가 보통 4GB+이라 FAT32의 단일 파일 한계(4GB)를 넘김
- 시스템 드라이브(C:)가 작다면 **D:** 또는 **외장 NVMe** 사용 (`HF_HOME`로 캐시까지 옮길 것 — 아래 1.4)

### 1.2 Python 3.10+ 설치

PowerShell에서:

```powershell
# 이미 설치되어 있으면 스킵
winget install --id Python.Python.3.12 --silent

# 확인
python --version
# Python 3.12.x 가 나와야 함
```

> winget이 없으면 https://www.python.org/downloads/windows/ 에서 직접 설치.
> **Add python.exe to PATH** 체크박스 반드시 켤 것.

### 1.3 huggingface_hub + hf_transfer 설치

```powershell
pip install --upgrade pip
pip install "huggingface_hub[hf_transfer]"
```

`hf_transfer`는 Rust로 작성된 고속 다운로더라 표준 HTTP 대비 5~10배 빠릅니다.

### 1.4 환경변수 설정

PowerShell **현재 세션**에서:

```powershell
# 빠른 전송 모드 (필수)
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"

# 캐시 위치를 D 드라이브로 (선택, 권장)
$env:HF_HOME = "D:\hf-cache"

# 텔레메트리 비활성 (선택)
$env:HF_HUB_DISABLE_TELEMETRY = "1"
```

영구 설정으로 두고 싶으면:

```powershell
[Environment]::SetEnvironmentVariable("HF_HUB_ENABLE_HF_TRANSFER", "1", "User")
[Environment]::SetEnvironmentVariable("HF_HOME", "D:\hf-cache", "User")
```

영구 설정 후 새 PowerShell 창을 열어야 적용됩니다.

### 1.5 Windows 절전 끄기 (필수)

다운로드 도중 슬립으로 들어가면 끊김. PowerShell **관리자 권한**:

```powershell
# AC 전원에서 슬립 안 함, 디스플레이만 꺼지게
powercfg /change standby-timeout-ac 0
powercfg /change disk-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

또는 GUI: **설정 → 시스템 → 전원 → 화면 및 절전 → "전원에 연결됨" 모두 "안 함"**.

### 1.6 Windows Defender 실시간 보호 폴더 제외 (선택, 강력 권장)

480GB 파일을 실시간 검사하면 IO가 폭사함. **설정 → 업데이트 및 보안 → Windows 보안 →
바이러스 및 위협 방지 → 설정 관리 → 제외 추가 또는 제거 → 폴더**:

- `D:\models` (또는 다운로드 받을 폴더)
- `D:\hf-cache` (HF_HOME으로 지정한 폴더)

---

## 2. 다운로드 절차

### 2.1 명령

PowerShell에서 (위 1.4의 환경변수가 설정된 상태):

```powershell
huggingface-cli download Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 `
    --local-dir "D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8" `
    --local-dir-use-symlinks False `
    --resume-download
```

옵션 설명:

| 옵션 | 의미 |
|---|---|
| `--local-dir <path>` | 받을 위치. 모델 파일들이 여기 펼쳐짐 |
| `--local-dir-use-symlinks False` | **NTFS 필수**. 일반 파일로 받음 (symlink는 관리자 권한 요구) |
| `--resume-download` | 끊어져도 이어서 받음 |

### 2.2 진행 상황

`huggingface-cli`가 진행률 바를 표시합니다. 약 480GB이므로 회선에 따라:

| 회선 | 예상 시간 |
|---|---|
| 100 Mbps | 약 11시간 |
| 500 Mbps | 약 2시간 (병목 = 디스크 쓰기) |
| 1 Gbps | 약 70분 (병목 = 디스크 쓰기) |
| 10 Gbps | 약 30~40분 (NVMe 한계) |

### 2.3 끊긴 경우

같은 명령을 다시 실행하면 됩니다. `--resume-download`가 부분 다운로드 파일을 이어받음.

### 2.4 다운로드 완료 후 디렉토리 모습

```
D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8\
├── config.json
├── generation_config.json
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── merges.txt
├── vocab.json
├── model.safetensors.index.json
├── model-00001-of-XXXXX.safetensors
├── model-00002-of-XXXXX.safetensors
├── ...
└── model-XXXXX-of-XXXXX.safetensors
```

(shard 개수는 모델 버전에 따라 다름; 대략 100~250개)

---

## 3. 다운로드 무결성 검증 (선택, 강력 권장)

### 3.1 빠른 점검 (Windows)

PowerShell에서 안에 핵심 파일들이 있는지:

```powershell
$dir = "D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8"
Test-Path "$dir\config.json"
(Get-ChildItem "$dir\*.safetensors").Count
"{0:N2} GB" -f ((Get-ChildItem $dir -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB)
```

- `config.json` 존재 → True
- safetensors 파일 개수 → 100 이상이면 정상
- 총 용량 → ~480 GB

### 3.2 SHA256 (Linux로 옮긴 후 권장)

Windows에서 SHA256은 `Get-FileHash`로 가능하지만 한 파일씩 느림.
Linux로 옮긴 후 한 번에 처리하는 편이 효율적이라 거기서 합니다 (§5 참조).

---

## 4. Linux 머신으로 옮기기

### 4.1 외장 SSD (권장 — 가장 빠르고 안전)

#### Windows에서 외장 SSD에 복사

```powershell
# 외장 NVMe가 E: 라고 가정 (NTFS or exFAT 포맷)
robocopy "D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8" `
         "E:\Qwen3-Coder-480B-A35B-Instruct-FP8" `
         /MIR /R:3 /W:5 /MT:8 /ETA
```

옵션:
- `/MIR` 미러링 (소스 = 대상)
- `/R:3 /W:5` 재시도 3번, 대기 5초
- `/MT:8` 8개 스레드 (NVMe면 더 빠름)
- `/ETA` 예상 종료 시각 표시

#### Linux 호스트에서 외장 SSD 마운트 + 복사

```bash
# Linux 호스트(22.04)에서. 외장 SSD가 /dev/sdc1로 인식되었다고 가정.
sudo mkdir -p /mnt/ext
sudo mount /dev/sdc1 /mnt/ext     # NTFS면 자동 ntfs-3g

# 번들 위치로 복사
mkdir -p LoopCoder/output/bundle/models
rsync -a --info=progress2 \
    /mnt/ext/Qwen3-Coder-480B-A35B-Instruct-FP8 \
    LoopCoder/output/bundle/models/

# 마운트 해제
sudo umount /mnt/ext
```

> **NTFS 마운트가 안 되면**: `sudo apt install ntfs-3g` 후 재마운트.
> **exFAT 마운트가 안 되면**: `sudo apt install exfat-fuse exfatprogs` 후 재마운트.

### 4.2 SMB 공유 (네트워크 경유, 외장 SSD 없을 때)

#### Windows에서 폴더 공유

1. `D:\models` 우클릭 → **속성** → **공유** 탭 → **고급 공유** → **이 폴더 공유** 체크
2. **공유 이름**: `models` (예시)
3. **권한** 버튼 → 자기 계정에 **읽기** 부여
4. Windows 호스트의 IP 확인: PowerShell에서 `ipconfig` → IPv4

#### Linux 호스트에서 마운트

```bash
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/win-models

# Windows 사용자명/비밀번호로 마운트
sudo mount -t cifs //<WIN_IP>/models /mnt/win-models \
    -o "username=<WIN_USER>,vers=3.0,iocharset=utf8"

# 복사 (네트워크라 시간 오래 걸림 — 100~300 MB/s)
mkdir -p LoopCoder/output/bundle/models
rsync -a --info=progress2 \
    /mnt/win-models/Qwen3-Coder-480B-A35B-Instruct-FP8 \
    LoopCoder/output/bundle/models/

sudo umount /mnt/win-models
```

### 4.3 rsync over SSH (WSL 사용)

WSL2에서 Linux의 rsync를 호출. 네트워크 대역폭 한계까지 갈 수 있고 재개도 가능.

```powershell
# Windows에서 WSL 켜기 (한 번만)
wsl --install -d Ubuntu

# WSL 안에서
rsync -avP --partial \
    /mnt/d/models/Qwen3-Coder-480B-A35B-Instruct-FP8 \
    user@linux-host:LoopCoder/output/bundle/models/
```

> **장점**: 끊겨도 `--partial`로 이어받기 가능.
> **속도**: 보통 100~300 MB/s. 10G 사설망이면 더 나옴.

### 4.4 방법 비교

| 방법 | 속도 | 무결성 | 재개 | 권장도 |
|---|---|---|---|---|
| 외장 USB3.2/Thunderbolt SSD | ~1 GB/s | ⭐⭐⭐ | △ (수동) | **★★★★★** |
| SMB cifs 마운트 | 100~300 MB/s | ⭐⭐ | ⭐⭐ | ★★★ |
| rsync over SSH (WSL) | 100~300 MB/s | ⭐⭐⭐ | ⭐⭐⭐ | ★★★★ |
| scp | 100~300 MB/s | ⭐⭐⭐ | ✗ | ★ (재개 안 됨) |
| 클라우드 경유(WebDAV/Drive) | ~50 MB/s | ⭐⭐ | ⭐ | ★ (느림) |

---

## 5. Linux에서 도착 검증

```bash
cd LoopCoder/output/bundle/models/Qwen3-Coder-480B-A35B-Instruct-FP8

# 핵심 파일들 존재
test -f config.json && echo "OK config.json"
test -f tokenizer.json && echo "OK tokenizer.json"
ls model-*.safetensors | wc -l   # 100 이상이어야 정상

# 총 크기
du -sh .
# ~ 480G 가 나와야 함

# 기본적인 JSON 파싱 OK인지
python3 -c "import json; print(json.load(open('config.json'))['model_type'])"
```

선택 — SHA256 자가 검증 (대략 5~10분):

```bash
sha256sum *.safetensors > /tmp/local.sha256
wc -l /tmp/local.sha256
# shard 개수와 일치해야 함
```

> **참고**: 우리 LoopCoder의 `bundle/in_vm/make_manifest.sh`가 어차피 모든 파일을
> 다시 SHA256 처리해서 `manifest.sha256`에 기록합니다.
> B300 노드의 `setup.sh` Stage 2 (manifest_verify)에서 자동으로 무결성 재검증합니다.
> 그러므로 §5의 SHA는 운반 중 깨짐을 잡는 용도일 뿐 필수는 아닙니다.

---

## 6. LoopCoder bundle.sh와 통합

### 6.1 디렉토리 위치

LoopCoder 번들의 모델은 다음 위치에 있어야 합니다:

```
LoopCoder/output/bundle/models/Qwen3-Coder-480B-A35B-Instruct-FP8/
```

위 §4에서 그대로 복사한 결과가 이 위치라면 끝.

### 6.2 bundle.sh 실행 시 모델 단계 스킵

```bash
cd /home/koopark/claude/KooDynaOptimizer/LoopCoder
bash bundle.sh --skip-model
```

이렇게 하면:
- `bundle/in_vm/collect_model.sh`가 호출되지 않음 (HF에서 다시 다운로드 X)
- 나머지(apt, wheels, vLLM .sif, sandbox .sif, source)만 Bundle VM에서 수집
- `make_manifest.sh`는 `/output/models/` 안에 이미 존재하는 파일들을 그대로 인덱스해서 SHA256 작성

### 6.3 검증 (Test VM)

이미 모델이 있는 상태에서 Test VM 검증:

```bash
bash test_setup.sh --bundle LoopCoder/output/bundle
```

Test VM의 `setup.sh --skip-gpu-stages` 모드는 모델 staging도 같은 디스크 공간 절약을 위해
`--skip-model-stage` 플래그를 자동 적용 — 여기서는 단지 파일 존재 여부와 manifest 일치만 봅니다.

### 6.4 B300 배포

```bash
# 호스트에서
rsync -avP --partial LoopCoder/output/bundle/ b300:/models/

# B300 노드에서
ssh b300
sudo bash /models/source/LoopCoder/setup.sh
# Stage 7 model_stage가 /models → /scratch/models 로 rsync
# Stage 2 manifest_verify가 SHA256 체크
```

---

## 7. 트러블슈팅

### 7.1 `huggingface-cli: command not found`

Python의 Scripts 폴더가 PATH에 없습니다. PowerShell에서:

```powershell
$env:PATH += ";$env:APPDATA\Python\Python312\Scripts"
# 또는 시스템 설치라면:
$env:PATH += ";C:\Users\$env:USERNAME\AppData\Roaming\Python\Python312\Scripts"
```

영구 적용: 시스템 환경변수 편집.

### 7.2 `OSError: [WinError 1314] A required privilege is not held by the client`

`--local-dir-use-symlinks True` (기본값)일 때 발생. **반드시 `False`로** 다시 실행하세요.

### 7.3 다운로드가 매우 느림 (수 MB/s)

- `HF_HUB_ENABLE_HF_TRANSFER=1`이 설정됐는지 확인
- Windows Defender 실시간 검사 → 폴더 제외에 추가 (§1.6)
- ISP가 HF CDN을 throttle하는 경우 있음 — VPN으로 우회

### 7.4 `Disk full` (다운로드 도중)

디스크 공간 부족. 480GB + 임시 100GB 정도 여유 필요.

### 7.5 SafetensorError: file is too large

FAT32 디스크에 받았을 가능성. NTFS/exFAT로 포맷된 디스크에 다시 받으세요.

### 7.6 옮기는 도중 RTC corruption

NTFS 파일을 cifs로 옮길 때 일부 문자가 깨질 수 있음 (드뭄). 외장 SSD로 옮기는 게 가장 안전.

### 7.7 huggingface_hub Python에서 `huggingface-cli download` 대신

```powershell
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8', local_dir=r'D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8', local_dir_use_symlinks=False, resume_download=True)"
```

CLI와 동일하게 동작.

### 7.8 큰 파일 일부만 다운받고 죽는 경우 (메모리 부족)

`hf_transfer`가 메모리 사용이 많을 수 있음. 환경변수로 끄고 재시도:

```powershell
$env:HF_HUB_ENABLE_HF_TRANSFER = "0"
huggingface-cli download ... --resume-download
```

느려지지만 메모리 안정적.

---

## 8. FAQ

### Q1. Windows에서 받은 파일을 그대로 B300에 올려도 되나요?

→ **네**, 그대로 됩니다. 단 §4에서 Linux 호스트(번들 머신)를 거쳐가야 `make_manifest.sh`로
SHA256과 manifest를 만들 수 있습니다. Windows에서 직접 B300으로 보내는 것도 가능하지만
그러면 manifest를 B300에서 만들어야 해서 절차가 꼬입니다.

### Q2. 받는 도중 PC를 재부팅해도 되나요?

→ 네. `--resume-download`로 다시 받으면 끊긴 곳부터 재개됩니다.

### Q3. 모델 ID가 바뀌면?

→ `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`만 다른 ID로 바꿔서 명령 재실행. 이미 받은 다른
모델 폴더는 건드리지 않음. 본 LoopCoder의 `install.yaml`의 `model.id`도 같이 갱신해야 합니다.

### Q4. 다운로드 파일이 진짜 안 깨졌는지 확실히 확인하려면?

→ HF의 모델 페이지(huggingface.co/Qwen/Qwen3-Coder-...) → **Files and versions** → 각 파일의
LFS 메타데이터 옆에 SHA256이 표시됨. `Get-FileHash`로 비교 가능. 다만 너무 번거로움 —
우리 manifest 검증 + B300의 stage 2 검증이면 충분합니다.

### Q5. 다른 양자화(BF16, INT4) 받고 싶다면?

→ 모델 ID만 바꾸면 됨. 예:
- `Qwen/Qwen3-Coder-480B-A35B-Instruct` (BF16, ~960GB)
- `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` (FP8, ~480GB) ← 본 프로젝트 디폴트
- `Qwen/Qwen3-Coder-480B-A35B-Instruct-AWQ` (AWQ4 등, 더 작음)

`config/install.yaml`의 `model.id`와 `vllm.yaml`의 `quantization`을 일치시켜야 합니다.

### Q6. 모델만 따로 보관하고 다른 번들은 자주 바꾸고 싶은데?

→ 가능합니다. 모델 디렉토리는 한 번 만들면 거의 안 바뀝니다. `bundle.sh --skip-model`로
번들의 다른 부분만 갱신하면 모델은 그대로 유지됩니다.

---

## 9. 한눈에 보는 요약

```
[Windows PC]
 1. python + huggingface_hub[hf_transfer] 설치
 2. powercfg로 절전 끄기
 3. Defender 폴더 제외
 4. $env:HF_HUB_ENABLE_HF_TRANSFER = "1"
 5. huggingface-cli download Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8 \
        --local-dir D:\models\Qwen3-Coder-480B-A35B-Instruct-FP8 \
        --local-dir-use-symlinks False --resume-download
 6. (외장 SSD에 robocopy)

[Linux 호스트]
 7. 외장 SSD 마운트 → rsync로 LoopCoder/output/bundle/models/ 로 복사
 8. bash bundle.sh --skip-model     # 모델 외 나머지 수집
 9. bash test_setup.sh              # Test VM에서 검증

[B300]
10. rsync LoopCoder/output/bundle/ b300:/models/
11. ssh b300 sudo bash /models/source/LoopCoder/setup.sh
12. loopcoder run --plan ...
```

---

## 10. 참조

- HuggingFace download docs: https://huggingface.co/docs/huggingface_hub/guides/download
- `hf_transfer`: https://github.com/huggingface/hf_transfer
- 모델 카드: https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8
- LoopCoder PLAN.md §3 (Offline Bundle Workflow)
- LoopCoder INSTALL.md (전체 설치 절차)
