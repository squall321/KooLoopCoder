# 구현 현황 (PROGRESS) — LoopCoder

> **갱신 원칙**: 파일 생성·수정·삭제 또는 검증 명령 실행 직후 **즉시** 본 문서를 갱신.
> "다 만들고 나서 한꺼번에"는 금지. PLAN.md §0 (철칙) 참조.
>
> **상태**: `☐ todo` / `▣ wip` / `△ untested` / `■ done` / `✗ failed` / `⏸ blocked`

---

## 요약 대시보드 (rev.6)

| Phase | 작업수 | done | △ untested | todo | blocked |
|---|---|---|---|---|---|
| Phase 0 (의사결정) | 23 | 23 | 0 | 0 | 0 |
| Phase 1A (Bundle VM 스크립트) | 8 | 1 | 7 | 0 | 0 |
| Phase 1A' (Test VM 스크립트) | 9 | 0 | 9 | 0 | 0 |
| Phase 1B (VM 안 수집 스크립트) | 8 | 0 | 8 | 0 | 0 |
| Phase 1C (setup.sh 14 stages) | 14 | 0 | 14 | 0 | 0 |
| Phase 1D (config·systemd·헬퍼) | 10 | 4 | 6 | 0 | 0 |
| Phase 2 (에이전트 코어 26 모듈) | 26 | 26 | 0 | 0 | 0 |
| Phase 2D (Claude Code 패턴 CC1~CC14) | 14 | 14 | 0 | 0 | 0 |
| Phase 3 (검증) | 17+ | 9 단위 | 0 | 8 E2E (B300 필요) | 8 |
| **Total** | **129+** | **77** | **44** | **8** | **8** |

진행률: 77 / 129 = **59.7%** (단위테스트 통과 기준 ■, B300/VM 실증 필요한 것은 △)

**단위테스트: 102/102 PASS** — config 12 / plan_schema 11 / plan_topo 4 / tokens 3 / tools_fs 16 / hooks 4 / todo 5 / shell_bg 7 / conventions 4 / reminders 2 / context 4 / state_store 6 / sandbox 5 / verifier 10 / snapshot 5 / controller_integration 4. 컨트롤러 통합테스트(mock LLM) 4개 시나리오 모두 통과: 1-iter pass, retry-then-pass, max-iter fail-clean, 토큰 SQLite 영속화.

---

## 디렉토리 경로 합의 (rev.7 — 프로젝트 서브폴더로 통합)

> 모든 빌드 산출물은 `LoopCoder/output/` 안으로 모음. `.gitignore`에 등록.
> 환경변수 / CLI 플래그로 다른 위치(예: `/data`)로 override 가능하지만 디폴트는 프로젝트 안.

| 경로 | 용도 |
|---|---|
| `LoopCoder/output/vm-disks/` | Bundle/Test VM의 qcow2, seed ISO |
| `LoopCoder/output/bundle/` | 번들 출력 (apt/wheels/containers/models/source/manifest) |
| `LoopCoder/output/test-scratch/` | Test VM `/scratch` 시뮬레이션 영역 |
| `LoopCoder/output/test-results/` | Test VM 검증 리포트 |
| `LoopCoder/output/tiny-test/` | 작은 모델 end-to-end 테스트 산출물 |
| `LoopCoder/.venv/` | Python 가상환경 (gitignored) |
| `LoopCoder/` | 본 프로젝트 소스 루트 |

---

## Phase 0 — 의사결정 (D1~D23 모두 ■ done — 생략)

상세는 PLAN.md §2 Decision Log 참조.

---

## Phase 1A — Bundle VM 프로비저닝

| ID | 산출물 | 상태 | 비고 |
|---|---|---|---|
| V1 | virt-manager/libvirt 확인 | ■ done | 사용자 확정 |
| V2 | `bundle/vm/cloud-init/user-data` | △ untested | 작성됨 |
| V3 | `bundle/vm/cloud-init/meta-data` | △ untested | 작성됨 |
| V4 | `bundle/vm/domain.xml.template` | △ untested | virtiofs 2개 + virtio net |
| V5 | `bundle/vm/setup_vm.sh` | △ untested | virt-install + cloud-init seed ISO |
| V6 | `bundle/vm/start_vm.sh` | △ untested | IP 자동탐색 + ssh config 자동작성 |
| V7 | `bundle/vm/run_in_vm.sh` | △ untested | exit 코드 전달 |
| V8 | `bundle/vm/destroy_vm.sh` | △ untested | undefine + storage 제거 |

## Phase 1A' — Test VM 프로비저닝

| ID | 산출물 | 상태 | 비고 |
|---|---|---|---|
| T1 | `bundle/test_vm/cloud-init/user-data` | △ untested | 인터넷 X (패키지 X) |
| T2 | `bundle/test_vm/cloud-init/meta-data` | △ untested | |
| T3 | `bundle/test_vm/domain.xml.template` | △ untested | isolated network + virtiofs ×2 |
| T4 | `bundle/test_vm/setup_test_vm.sh` | △ untested | isolated libvirt network 자동 생성 |
| T5 | `bundle/test_vm/start_test_vm.sh` | △ untested | 인터넷 차단 어설션 |
| T6 | `bundle/test_vm/run_setup_in_vm.sh` | △ untested | setup.sh --skip-gpu-stages 실행 |
| T7 | `bundle/test_vm/assert_setup_results.sh` | △ untested | 7가지 post-condition 검증 |
| T8 | `bundle/test_vm/destroy_test_vm.sh` | △ untested | VM + isolated network 제거 |
| T9 | `test_setup.sh` (호스트 오케스트레이터) | △ untested | end-to-end + markdown 리포트 |

## Phase 1B — VM 안 번들 수집

| ID | 산출물 | 상태 | 비고 |
|---|---|---|---|
| B0 | 호스트 오케스트레이터 `bundle.sh` | △ untested | 8개 단계 통합, dry-run 지원 |
| B1 | `bundle/in_vm/bootstrap.sh` | △ untested | apt-rdepends, hf_transfer, apptainer |
| B2 | `bundle/in_vm/collect_apt.sh` | △ untested | apt-rdepends 의존성 closure |
| B3 | `bundle/in_vm/collect_wheels.sh` | △ untested | manylinux_2_28, py3.12, 자가검증 |
| B4 | `bundle/in_vm/collect_vllm_image.sh` | △ untested | docker → apptainer build, import smoke |
| B5 | `bundle/in_vm/collect_sandbox_image.sh` | △ untested | py3.12-slim 기반, ruff/mypy 포함 |
| B6 | `bundle/in_vm/collect_model.sh` | △ untested | hf_transfer, config.json 검증 |
| B7 | `bundle/in_vm/make_manifest.sh` | △ untested | manifest.yaml + sha256, 자가검증 |

## Phase 1C — setup.sh (B300 오프라인)

| Stage | 이름 | 상태 | 비고 |
|---|---|---|---|
| S0 | preflight | △ untested | 디스크 30GB 검증 포함 |
| S1 | hw_check | △ untested | --skip-gpu-stages 인식 |
| S2 | manifest_verify | △ untested | manifest.sha256 자동 검증 |
| S3 | apt_offline | △ untested | bundle/apt/*.deb |
| S4 | apptainer | △ untested | --version 확인 |
| S5 | python_env | △ untested | python3.12 venv |
| S6 | agent_deps | △ untested | --no-index, loopcoder editable install |
| S7 | model_stage | △ untested | rsync /models→/scratch |
| S8 | vllm_image | △ untested | sif 복사 + import smoke |
| S9 | systemd_unit | △ untested | template 렌더링 + enable |
| S10 | start_vllm | △ untested | curl /v1/models 폴링 (15분) |
| S11 | smoke_test | △ untested | "1+1=" → "2" |
| S12 | agent_install | △ untested | /usr/local/bin/loopcoder 심볼릭 |
| S13 | summary | △ untested | 사용법 출력 |

## Phase 1D — 설정/시스템 헬퍼

| ID | 산출물 | 상태 | 검증 |
|---|---|---|---|
| C1 | `config/install.yaml.example` | ■ done | Pydantic 로드 통과 |
| C2 | `config/vllm.yaml.example` | ■ done | Pydantic 로드 통과 |
| C3 | `config/loopcoder.yaml.example` | ■ done | Pydantic 로드 통과 |
| C4 | `systemd/vllm.service.template` | △ untested | sed 치환 변수 7종 |
| C5 | `containers/vllm.def` | △ untested | docker:vllm/vllm-openai:latest |
| C6 | `containers/loopcoder-sandbox.def` | △ untested | python:3.12-slim 기반 |
| C7 | `scripts/healthcheck.sh` | △ untested | 3중 체크 |
| C8 | `scripts/benchmark.sh` | △ untested | vllm bench |
| C9 | `scripts/uninstall.sh` | △ untested | --purge-data 옵션 |
| C10 | `scripts/make_apptainer_images.sh` | ■ done | syntax OK |

---

## Phase 2 — 에이전트 모듈 (loopcoder/)

### 2A. 코어 (config / plan / llm)
| 모듈 | 파일 | 상태 |
|---|---|---|
| M1 | `loopcoder/cli.py` | ■ done |
| M2 | `loopcoder/config.py` | ■ done |
| M3 | `loopcoder/plan/schema.py` | ■ done |
| M4 | `loopcoder/plan/parser.py` | ■ done |
| M5 | `loopcoder/plan/topo.py` | ■ done |
| M6 | `loopcoder/llm/client.py` | ■ done |
| M7 | `loopcoder/llm/prompts.py` | ■ done (CC8/CC10 스타일 적용) |
| M8 | `loopcoder/llm/tokens.py` | ■ done |
| M9 | `loopcoder/llm/context.py` ★ | ■ done (요약 금지 정책) |

### 2B. 도구 / 샌드박스
| 모듈 | 파일 | 상태 |
|---|---|---|
| M10 | `loopcoder/tools/base.py` + `registry.py` | ■ done (훅 통합) |
| M11 | `loopcoder/tools/fs.py` | ■ done (CC2 라인번호 + CC3 read-before-write) |
| M12 | `loopcoder/tools/shell.py` | ■ done (CC11 cap + CC12 background) |
| M13 | `loopcoder/tools/git.py` | ■ done |
| M14 | `loopcoder/tools/tests.py` | ■ done |
| M15 | `loopcoder/sandbox/base.py` | ■ done |
| M16 | `loopcoder/sandbox/apptainer.py` | ■ done |
| M17 | `loopcoder/sandbox/host.py` | ■ done |

### 2C. 루프 / 상태 / UI
| 모듈 | 파일 | 상태 |
|---|---|---|
| M18 | `loopcoder/loop/controller.py` | ■ done (CC4/CC5/CC7/CC14 통합) |
| M19 | `loopcoder/loop/verifier.py` | ■ done |
| M20 | `loopcoder/loop/critic.py` | ■ done |
| M21 | `loopcoder/loop/strategy.py` | ■ done |
| M22 | `loopcoder/state/store.py` (SQLite + todos 테이블) | ■ done |
| M23 | `loopcoder/state/snapshot.py` | ■ done |
| M24 | `loopcoder/state/replay.py` | ■ done |
| M25 | `loopcoder/ui/tty.py` | ■ done |
| M26 | `loopcoder/ui/report.py` | ■ done |

신규(CC):
- `loopcoder/tools/hooks.py` (CC6) ■ done
- `loopcoder/tools/todo.py` (CC5) ■ done
- `loopcoder/tools/spawn_agent.py` (CC7) ■ done
- `loopcoder/tools/meta.py` ■ done
- `loopcoder/loop/conventions.py` (CC14) ■ done
- `loopcoder/loop/reminders.py` (CC4) ■ done

## Phase 2D — Claude Code 패턴 통합 (CC1~CC14)

| ID | 패턴 | 상태 |
|---|---|---|
| CC1 | Edit unique-match | ■ done (기존) |
| CC2 | Read 라인번호 + offset/limit | ■ done |
| CC3 | Write 사전 Read 강제 | ■ done |
| CC4 | system-reminder 동적 주입 | ■ done |
| CC5 | TodoWrite 도구 | ■ done |
| CC6 | Pre/Post tool-use 훅 | ■ done |
| CC7 | Sub-agent (Task) | ■ done |
| CC8 | System prompt 리라이트 | ■ done |
| CC9 | Goal-driven loop | ■ done (기존) |
| CC10 | "Don't add unrequested" | ■ done |
| CC11 | Output cap + tail-bias | ■ done (기존) |
| CC12 | Bash 백그라운드 + 모니터링 | ■ done |
| CC13 | Plan mode | ■ done (plan.yaml) |
| CC14 | 컨벤션 자동 로드 | ■ done |

---

## Phase 3 — 검증

### 3A. 단위 테스트 (모두 ■ done — 93/93 통과)

| 파일 | 테스트수 | 상태 |
|---|---|---|
| test_config.py | 12 | ■ |
| test_plan_schema.py | 11 | ■ |
| test_plan_topo.py | 4 | ■ |
| test_tokens.py | 3 | ■ |
| test_tools_fs.py | 16 | ■ |
| test_hooks.py | 4 | ■ |
| test_todo.py | 5 | ■ |
| test_shell_bg.py | 7 | ■ |
| test_conventions.py | 4 | ■ |
| test_reminders.py | 2 | ■ |
| test_context.py | 4 | ■ |
| test_state_store.py | 6 | ■ |
| test_sandbox.py | 5 | ■ |
| test_verifier.py | 10 | ■ |
| **Total** | **93** | **93 PASS** |

### 3B. 통합 (E2E) — 실제 vLLM/B300 필요
| ID | 시나리오 | 상태 |
|---|---|---|
| E2E-1 | 알려진 버그 수정 | ⏸ blocked (vLLM 필요) |
| E2E-2 | 신규 기능 (FastAPI hello) | ⏸ blocked |
| E2E-3 | 리팩토링 | ⏸ blocked |
| E2E-4 | 무한루프 방지 | ⏸ blocked |
| E2E-5 | 거짓 보고 검출 | ⏸ blocked |
| E2E-6 | 롤백 | ⏸ blocked |
| E2E-7 | 컨텍스트 보존 | ⏸ blocked |
| E2E-8 | 오프라인 (Test VM 자동 검증) | △ untested (test_setup.sh 작성됨) |

### 3C. 수락 게이트
- [x] Python 패키지 import OK (전체 39 모듈)
- [x] 단위 테스트 100% (93/93)
- [x] 모든 셸 스크립트 syntax OK (19/19)
- [x] 3개 YAML 설정 Pydantic 검증 통과
- [x] 데모 plan.yaml 스키마 통과
- [ ] bundle.sh 22.04 호스트 통과 (실제 VM 필요)
- [ ] VM 24.04 안에서 collect_*.sh 통과 (실제 VM 필요)
- [ ] manifest 호스트 재검증 OK (실제 번들 필요)
- [ ] test_setup.sh가 Test VM에서 자동 통과
- [ ] setup.sh B300 통과 (수동, B300 필요)
- [ ] E2E-1~8 통과
- [ ] INSTALL.md 따라하기 성공
- [x] `loopcoder config validate` 통과
- [x] `loopcoder --version` 동작
- [ ] 24h 안정성

---

## 변경 이력 (append-only)

| 시각 | 변경 |
|---|---|
| 2026-05-05 | PROGRESS.md 초안 |
| 2026-05-05 | D1~D14 확정 |
| 2026-05-05 | D15(LoopCoder)/D16, D17~D19 추가 |
| 2026-05-05 | Phase 1A/1B 분리, V1~V8/B0~B7 추가 |
| 2026-05-05 | koocode → loopcoder |
| 2026-05-05 | D20~D23 추가 (Test VM, 디스크 경로) |
| 2026-05-05 | Phase 1A' (Test VM, T1~T9) 추가 |
| 2026-05-05 | V1 사용자 확정 → ■ done |
| 2026-05-05 | Python 3.12 venv + 의존성 설치, loopcoder --version=0.1.0 |
| 2026-05-05 | Phase 2 코어 26 모듈 + 단위테스트 30/30 통과 |
| 2026-05-05 | Phase 2D 추가 — Claude Code 패턴 14개 통합 |
| 2026-05-05 | CC2~CC8/CC10/CC12/CC14 모두 ■ done — 도구 23개, 훅 7건, 단위테스트 93/93 |
| 2026-05-05 | Phase 1C(setup.sh 14 stages) + Phase 1D(C1~C10) 작성 — 모두 syntax OK, YAML 3종 검증 |
| 2026-05-05 | Phase 1A/1A'/1B 모든 셸 스크립트 작성 — 19/19 syntax OK |
| 2026-05-05 | INSTALL.md, examples/plan_simple.yaml, demo workspace test fixture 추가 |
| 2026-05-05 | 종합 검증: 93 unit tests + 19 shell scripts + 3 YAML configs + demo plan + 23 tools + 7 hooks 모두 OK |

---

## 작업 로그 (실시간)

- 2026-05-05 PROGRESS rev.5 갱신, V1 done.
- 2026-05-05 Python 3.12 venv 생성, 의존성 설치, loopcoder CLI 동작 확인.
- 2026-05-05 Phase 2 코어 26 모듈 작성 (cli/config/plan/llm/tools/sandbox/state/loop/ui).
- 2026-05-05 단위테스트 30/30 → 93/93 통과까지 확장.
- 2026-05-05 PLAN §12-α 추가, Phase 2D (CC1~CC14) 14개 패턴 모두 통합.
- 2026-05-05 신규 도구: hooks, todo, spawn_agent, run_shell_background 외 4종.
- 2026-05-05 controller.py 재작성: TodoList + BackgroundJobs + conventions + reminders 통합.
- 2026-05-05 system prompt를 Claude Code 스타일로 리라이트 (직접·명령형).
- 2026-05-05 Phase 1C setup.sh 14 stages + --skip-gpu-stages 모드 작성.
- 2026-05-05 Phase 1A bundle/vm/* 4개, Phase 1B bundle/in_vm/* 7개 작성.
- 2026-05-05 Phase 1A' bundle/test_vm/* 6개 + 호스트 오케스트레이터 test_setup.sh 작성.
- 2026-05-05 Phase 1D config 3종 + systemd template + 4개 헬퍼 스크립트 + 2개 .def 작성.
- 2026-05-05 INSTALL.md, examples/plan_simple.yaml, demo workspace fixture 추가.
- 2026-05-05 모든 셸 스크립트 syntax check 통과 (19/19).
- 2026-05-05 종합 검증: 모든 import OK, 93 단위테스트 통과, 23 tools 등록, configs 3종 + demo plan 통과.
- 2026-05-05 SnapshotManager 테스트 5개 추가 (init/snapshot/diff/revert).
- 2026-05-05 LoopController 통합테스트 4개 추가 (mock LLM 기반): 1-iter pass / retry-then-pass / max-iter clean fail / 토큰 영속화.
- 2026-05-05 CLI dev-friendliness: LOOPCODER_YAML 환경변수 override 추가, SessionStore 권한 에러 메시지 개선.
- 2026-05-05 최종: 102/102 단위테스트 통과, CLI list/config validate 동작 확인.

---

## 다음 액션 (Next Up — 사용자 깨신 후)

> **현 상태**: 코드 100% 작성 완료. 단위 검증(host-level) 100%. VM/B300 실증만 남음.

**우선순위 1 — VM 실행 검증 (호스트에서 가능)**:
1. **V5 + V6 실행**: `sudo bash bundle/vm/setup_vm.sh loopcoder-bundle-vm /data/loopcoder-vm /data/loopcoder-bundle loopcoder` → VM 부팅 + ssh 통과 확인
2. **B1~B7 dry-run**: VM 안에서 collect_*.sh 한 번씩 실행 (모델 다운로드는 시간 오래걸림 → 우선 apt/wheels/containers만)
3. **bundle.sh 풀 실행** (모델 제외): `bash bundle.sh --skip-model`
4. **T1~T9 (test_setup.sh) 실행**: bundle 일부만으로도 stage 0~9, 12, 13 검증 가능

**우선순위 2 — 모델 다운로드 (시간 오래)**:
- **B6** collect_model.sh: 480GB FP8, 시간 많이 소요. bundle 머신에서 백그라운드로

**우선순위 3 — B300 실배포 (네트워크 차단 후)**:
- 번들 rsync → B300:/models/
- setup.sh 실행 (S0~S13 풀 스택)
- E2E-1 ~ E2E-8 시나리오 돌리기

**선택 사항 — 추가 강화**:
- E2E-7 (컨텍스트 보존) 시나리오 자동화
- 24h 안정성 워크로드
- 추가 plan 예시 (FastAPI hello, refactor 등) — ✅ 추가됨

---

## 작업 로그 (continued)

- 2026-05-05 docs/manuals/model-download-windows.md 작성 — Windows PC에서 480GB 모델 사전 다운로드 + Linux 호스트 전송 매뉴얼.
- 2026-05-05 디렉토리 재구조: `/data/loopcoder-*` → `LoopCoder/output/*`, .gitignore 추가, bundle.sh / test_setup.sh / INSTALL.md / 매뉴얼 일괄 갱신, D24/D25 추가.
- 2026-05-05 logsetup.py 추가 (구조화 로깅 + JSON 로테이팅 핸들러), CLI에 `--log-level` `--log-dir` 통합.
- 2026-05-05 CLI dry-run 모드 (`--dry-run`) — LLM 호출 없이 acceptance만 실행.
- 2026-05-05 CLI export 명령 (`loopcoder export <id> --out file.tar.gz`) — 세션 trace tar.gz 패키징.
- 2026-05-05 추가 plan 예시 2종: examples/plan_refactor.yaml (4 goals), plan_fastapi_hello.yaml (3 goals).
- 2026-05-05 작은 모델 인프라: config/install.yaml.tiny + vllm.yaml.tiny + examples/tiny-end-to-end.sh.
- 2026-05-05 **실제 Qwen2.5-Coder-0.5B-Instruct 모델 다운로드 성공** — 1분 17초, ~950MB, output/tiny-test/models/ 에 정상 배치.
- 2026-05-05 Windows 다운로드 스크립트 신규 폴더 scripts/windows/: Download-Model.ps1 + Download-Model.bat (3 mode) + Verify-Model.ps1 + README.md.
- 2026-05-05 단위테스트: test_cli.py 7개 추가 (CliRunner 기반). config 환경변수 동적 평가로 변경. **109/109 통과**.
- 2026-05-05 EventBus 추가 + controller에 9개 emit 지점 (session/goal/iter, tool.called, verify.failed).
- 2026-05-05 **HTTP API** `loopcoder/api/{server,runner,models}.py` — FastAPI 16 라우트, SSE 라이브 스트림, Bearer 토큰 옵션 인증. 라이브 서버 검증: `/v1/health` `/v1/tools(23개)` `/v1/sessions` 응답. 단위테스트 10/10.
- 2026-05-05 **MCP 서버** `loopcoder/mcp/server.py` — stdio + SSE transport, ToolRegistry → MCP Tool 23개 자동 매핑. 단위테스트 4/4 (list_tools, call_tool, unknown).
- 2026-05-05 CLI 신규: `loopcoder serve` (FastAPI/uvicorn), `loopcoder mcp serve` (stdio/sse).
- 2026-05-05 **VS Code 확장** `vscode-extension/`: TS 4 모듈(api/sessionsTreeView/toolsTreeView/extension), package.json(7 commands + 2 views + 3 settings), README. tsc 0 에러 컴파일.
- 2026-05-05 최종: **123/123 단위테스트** + 라이브 API 검증 + MCP 빌드 검증 + TS 컴파일 OK.
- 2026-05-06 HANDOFF.md 작성 (15절, 457줄) — 새 AI/엔지니어를 위한 단일 onboarding 문서.
- 2026-05-06 PLAN/PROGRESS/HANDOFF.md를 LoopCoder/ 루트로 이동 (repo 단일화). HANDOFF의 상대경로 보정. .gitignore에 node_modules/, dist/, *.vsix 추가.
- 2026-05-06 **GitHub 푸시 완료**: `git@github.com:squall321/KooLoopCoder.git`, main 브랜치, 135 파일 / 13,477 lines, commit `f83fee8`. https://github.com/squall321/KooLoopCoder
- 2026-05-06 LICENSE (MIT) + CHANGELOG.md (0.1.0 release notes) + .github/workflows/test.yml (pytest+coverage / shell syntax / TS compile, 3 jobs).
- 2026-05-06 logsetup 통합: controller / verifier / llm.client / tools.registry — 핵심 4개 모듈에서 `get_logger(__name__)` 사용.
- 2026-05-06 워크스페이스 fixtures 추가: `examples/workspaces/refactor_calculator/` (200줄 monolithic + 6 pytest, 모두 PASS), `examples/workspaces/fastapi_hello/` (conftest + requirements).
- 2026-05-06 **Mock-LLM E2E 시나리오 4종 작성 + 통과** (`agent/tests/e2e_mock/`, 6 tests):
  - E2E-4 max_iter 도달시 깔끔한 fail (status=failed, iterations==max_iter, 워크스페이스 변동 없음)
  - E2E-5 거짓 submit_goal 차단 (3회 lying → 모두 fail) + 진짜 작업시 통과 (lie/lie/truth → iter 3 PASS)
  - E2E-6 rollback 시 git tag 보존 + 첫 골 통과 후 두번째 골이 rollback_after=3 도달
  - E2E-7 verify_log SQLite 영속성 + ContextBuilder 우선순위 정적 검증 (verify_log < attempt 보장)
- 2026-05-06 ruff 통과: 19개 위반 모두 수정 (4개 자동, 15개 import 순서 정리). 0 errors.
- 2026-05-06 **최종 테스트 매트릭스: 129/129 PASS** (단위 123 + mock-E2E 6).
