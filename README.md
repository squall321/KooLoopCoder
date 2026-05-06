# LoopCoder

LoopCoder는 자체 호스팅 LLM(예: Qwen3-Coder-480B FP8 on vLLM)을 사용해
사용자가 작성한 plan(목표·검증조건 명세)을 보고 코드를 점진적으로 작성·디버깅하며
모든 goal이 검증 통과할 때까지 반복하는 에이전트입니다.

## 빠른 시작 (B300 서버)

```bash
sudo bash setup.sh
loopcoder run --plan examples/plan_simple.yaml
```

## 빠른 시작 (개발 머신, 호스트 22.04)

```bash
# 인터넷 머신에서 번들 빌드 (24.04 VM 사용)
bash bundle.sh

# Test VM에서 setup.sh 자동 검증 (인터넷 차단 + GPU 없음)
bash test_setup.sh

# 통과하면 B300으로 전송
rsync -avP /data/loopcoder-bundle/ b300:/models/
```

## 구성

- `bundle.sh` — 인터넷 가능 머신용 번들 빌더 (24.04 VM 안에서 수집)
- `test_setup.sh` — Test VM에서 setup.sh 자동 검증
- `setup.sh` — B300 노드용 오프라인 설치 스크립트
- `agent/loopcoder/` — Python 에이전트 패키지
- `config/*.yaml.example` — 설정 템플릿 (install/vllm/loopcoder)

상세 계획은 상위 디렉토리의 `PLAN.md`를, 진행 현황은 `PROGRESS.md`를 참조하세요.

## 주요 특징

- **검증 외부화**: LLM이 "다 됐다"고 보고해도 acceptance 명령이 외부에서 실제 실행되어 통과해야 done
- **무한 루프 안전장치**: iter/시간/토큰 한도 + 연속 실패시 자동 롤백
- **컨텍스트 보존**: verify 로그·diff는 절대 요약·잘림 없이 컨텍스트에 유지 (1M 활용)
- **오프라인 친화**: B300이 인터넷 없어도 동작, 모든 의존성을 사전 번들링
- **Apptainer 샌드박스**: Docker 의존 없음, HPC 친화

## 라이선스

MIT
