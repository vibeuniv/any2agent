# self-verification Design Document

> **Summary**: 태스크 기반 자체 검증(eval) 하네스 — 생성·실행·채점·게이트·repair 피드백
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-05
> **Status**: Draft
> **Planning Doc**: [self-verification.plan.md](self-verification.plan.md)

---

## 1. Overview

### 1.1 Design Goals

1. **실제 완수 능력 측정**: 도구 선택률이 아니라, 실제 `run_chat` 루프가 라이브 API에 대해
   멀티스텝 태스크를 완수하는 비율을 측정한다.
2. **검증 가능한 채점 우선**: LLM-judge는 보조. 1차 채점은 결정적 체크(호출된 도구,
   read 도구로 최종 상태 재조회, 답변 포함 문자열)로 한다.
3. **repair 루프에 접속**: 실패 트랜스크립트가 기존 `enrich`/`synth_params`의 입력이 되어
   "평가 → 도구 개선 → 재평가" 사이클을 완성한다 (아티클의 "let agents optimize tools").
4. **기존 규율 준수**: honest report(잔여 갭 정직 보고), LLM call budget, 키 없을 때
   graceful degradation, write/danger 자동 호출 금지(명시 opt-in 제외) — 기존 connect의
   설계 원칙을 그대로 따른다.

### 1.2 Design Principles

- **최소 침습**: `core/agent.py` 수정은 auto-confirm 분기 한 곳(±6 LOC). 러너는 기존
  제너레이터 이벤트 스트림을 소비하는 별도 모듈로 격리.
- **사람이 curation 가능한 eval set**: 생성된 태스크는 `<project>.evals.json`으로 영속화 —
  자동 생성은 시작점이고, 팀이 다듬은 태스크가 회귀 테스트 자산이 된다.
- **무LLM 부분 동작**: provider key가 없어도 결정적 태스크 + 결정적 체크만으로 동작한다
  (judge·LLM 태스크 생성만 skip).

---

## 2. Architecture

### 2.1 Component Diagram

```
  any2agent eval (CLI)          any2agent connect --eval
        │                              │ (최종 게이트 1회)
        ▼                              ▼
┌──────────────────────────── verifier.task_eval ────────────────────────────┐
│                                                                            │
│  evals/tasks.py            evals/runner.py             evals/grader.py     │
│  ──────────────            ───────────────             ───────────────     │
│  load / generate     ───▶  run_task(task)        ───▶  grade(task, trace)  │
│  EvalTask[]                │                           │  1) checks(결정적) │
│  <project>.evals.json      ▼                           │  2) judge(LLM,보조)│
│                      core/agent.run_chat               ▼                   │
│                      (headless 소비,               EvalResult[]            │
│                       ctx.auto_confirm)                │                   │
│                            │                           ▼                   │
│                      dispatch → RestAdapter      report {passed, rate,     │
│                            │                             metrics, fails}   │
│                            ▼                                               │
│                       live API (verify_ctx 세션 passthrough)               │
└────────────────────────────────────────────────────────────────────────────┘
                                     │ 실패 트랜스크립트
                                     ▼
                     connect._repair 확장: enrich(문맥 포함) / synth_params
```

### 2.2 Data Flow

```
toolspec ─▶ 태스크 생성(LLM 또는 결정적) ─▶ evals.json
evals.json ─▶ 러너: run_chat 이벤트 수집 ─▶ EvalTrace
EvalTrace + task.checks ─▶ 그레이더 ─▶ EvalResult
EvalResult[] ─▶ task_eval 리포트(성공률·게이트) ─▶ CLI 출력 / connect repair
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `evals/tasks.py` | `spec.ToolSet`, `core/registry`, `llm_repair._ask` 패턴 | 태스크 생성·영속화 |
| `evals/runner.py` | `core/agent.run_chat`, `adapters/rest.RestAdapter` | 실행·트랜스크립트 수집 |
| `evals/grader.py` | `adapters/base.Adapter`(상태 체크), `core/registry`(judge) | 채점 |
| `verifier.task_eval` | `evals/*` | critic 통합·게이트 |
| `cli.py eval` | `config.AgentConfig`, `verifier` | 진입점 |

신규 서드파티 의존성: **없음** (litellm·httpx 기존 범위).

---

## 3. Data Model

`any2agent/evals/model.py` (dataclass, `spec.py`와 동일한 스타일/직렬화 관례)

```python
@dataclass
class EvalTask:
    id: str                          # "notes-read-1"
    prompt: str                      # 사용자 발화 그대로. 예: "장보기 메모를 만들고 목록에서 확인해줘"
    kind: str = "read"               # "read" | "write"
    expected_tools: list = field(default_factory=list)
                                     # [["notes_list"], ["search_notes"]] — OR-of-AND.
                                     # 각 안쪽 리스트는 유효한 해법 경로 하나(순서 무관 부분집합).
                                     # 아티클: "expected tool을 명시하되 복수 해법 허용"
    checks: list = field(default_factory=list)   # §3.1 Check 타입들. 비어 있으면 judge-only
    cleanup: list = field(default_factory=list)  # write 전용: [{"tool","args"}] 역방향 호출
    source: str = "generated"        # "generated" | "manual" — curation 추적

@dataclass
class EvalTrace:                     # 러너 산출물
    task_id: str
    steps: list                      # [{"tool","args","ok","status","error"}] 시간순
    answer: str                      # delta 누적 최종 답변
    rounds: int = 0                  # LLM 라운드 수 best-effort (0 = unknown)
    error: str = ""                  # 러너 수준 실패("skipped_budget" | LLM 에러 등)
    write_blocked: str = ""          # read 태스크가 시도한 write 도구명 (빈 문자열 = 없음)

@dataclass
class EvalResult:
    task_id: str
    success: bool
    reasons: list                    # 실패 사유 문자열들 (honest report용)
    checks_passed: int
    checks_total: int
    judge: dict | None               # {"pass": bool, "reason": str} | None(skip)
    metrics: dict                    # {"tool_calls", "wrong_tool_calls", "errors", "steps"}
```

### 3.1 Check 타입 (grader가 해석)

| type | 필드 | 판정 |
|------|------|------|
| `tool_called` | `any_of: [str]` | trace.steps의 도구명 중 하나 이상 포함 |
| `state` | `tool, args, expect_contains: str` | 채점 시점에 read 도구를 **직접 재호출**해 응답 직렬화 문자열에 포함 여부 확인. write 태스크의 핵심 검증 수단 |
| `answer_contains` | `value: str` (또는 `any_of`) | 최종 답변 텍스트 포함 여부 (대소문자 무시) |
| `no_errors` | — | trace.steps에 `ok=False`가 없음 |
| `judge` | `rubric: str` | LLM-judge에 위임 (§5.3) |

### 3.2 영속 포맷 `<project>.evals.json`

```json
{
  "project": "notes-api",
  "version": 1,
  "tasks": [ { "...EvalTask 필드..." } ]
}
```

toolspec과 나란히 프로젝트 루트에 저장. `--regen`이 아니면 기존 파일을 항상 우선한다
(수동 curation 보호). `AgentConfig`에 `evals_path()` 헬퍼 추가.

---

## 4. Module Specification

### 4.1 `evals/tasks.py` — 태스크 생성

```python
def load_or_generate(toolset, evals_path, n=8, regen=False, model_id=None) -> (valid, invalid)
# invalid = [{"id","why"}] — silent drop 금지 계약을 반환값으로 강제
```

1. `cfg.evals_path()` 존재 && not regen → 로드 후 반환.
2. **LLM 생성** (key 있을 때): domain별로 도구 묶음(이름·설명·파라미터 스키마·write 플래그)을
   제시하고 JSON 태스크 배열을 요청. 프롬프트 요구사항:
   - "단일 호출로 끝나지 않는, 2개 이상의 도구 호출이 필요한 현실적 사용자 요청"(아티클)
   - 각 태스크에 `expected_tools`(복수 경로 허용)와 결정적 `checks`를 반드시 포함
   - write 태스크는 payload에 `[a2a-eval]` 마커 문자열을 넣도록 prompt에 명시하고,
     대응하는 `cleanup`과 `state` 체크를 함께 생성
   - `llm_repair`와 동일한 `_ask`/`_json_obj` 패턴, **eval 전용 budget에서 차감**(§7)
3. **결정적 폴백** (key 없거나 LLM 실패): read 도구 기반 템플릿 —
   - 파라미터 없는 read 도구: "『{description}』에 해당하는 정보를 조회해서 요약해줘"
     + `tool_called` + `no_errors` 체크
   - path param 있는 read 도구: 목록 도구와 짝지어 "목록에서 하나 골라 상세를 확인해줘"
     (2-step) + 두 도구 `tool_called`
   - write 태스크는 폴백에서 생성하지 않음(안전)
4. 생성 결과를 `evals.json`으로 저장하고 `source: "generated"` 표기.

검증: 로드/생성 직후 `expected_tools`·`checks`·`cleanup`이 참조하는 도구명이 toolset에
실제 존재하는지 확인, 불일치 태스크는 `invalid`로 제외하고 리포트에 카운트(silent drop 금지).

### 4.2 `evals/runner.py` — 실행

```python
def run_task(task, toolset, adapter, model_id=None, verify_ctx=None,
             write_ok=False) -> EvalTrace
```

- `messages=[{"role":"user","content":task.prompt}]`로 `core/agent.run_chat` 제너레이터를
  소비. `delta`는 answer에 누적, `tool` 이벤트는 steps에 기록(`result.ok/status/error` 추출).
- **confirm 처리** — `core/agent.py` 최소 수정 1곳:

```python
# agent.py run_chat 내 confirm 분기 (기존 148-154행)
res = dispatch.execute(spec, args, adapter, ctx=ctx, confirmed=False)
if res.get("confirm_required"):
    if ctx.get("auto_confirm"):                       # ← 신규: eval 러너 전용
        res = dispatch.execute(spec, args, adapter, ctx=ctx, confirmed=True)
    else:
        yield {"type": "confirm", ...}; yield {"type": "done", ...}; return
```

  러너 정책:
  - `task.kind == "read"`: `auto_confirm`을 **넣지 않는다**. confirm 이벤트 수신 =
    read 태스크가 write 도구를 시도한 것 → `write_blocked=True`로 기록하고 종료
    (wrong-tool 신호이며 데이터는 안전).
  - `task.kind == "write"` && `write_ok`: `ctx["auto_confirm"]=True`. `write_ok`가
    아니면 write 태스크는 실행 자체를 skip (리포트에 `skipped_write`로 집계).
- **인증**: connect의 `verify_ctx`(ANY2AGENT_VERIFY_COOKIE/BEARER)를 ctx로 그대로 전달 —
  liveness와 동일하게 사용자 세션 passthrough로 실행된다.
- **cleanup**: write 태스크 종료 후(성공/실패 무관) `task.cleanup`의 호출을
  `dispatch.execute(confirmed=True)`로 best-effort 실행. 실패한 cleanup은 trace.error가
  아니라 결과 리포트의 `residue` 목록으로 정직하게 보고.
- 스텝 상한은 기존 `MAX_STEPS=8` 그대로(멀티스텝 태스크에 충분).

### 4.3 `evals/grader.py` — 채점

```python
def grade(task, trace, toolset, adapter, model_id=None, verify_ctx=None,
          judge_model=None) -> EvalResult
# toolset은 state 체크의 도구 조회에 필요; judge_model은 --judge-model 관통
```

판정 순서(싼 것 → 비싼 것):

1. `trace.error` 있으면 즉시 실패(사유 기록).
2. read 태스크의 `write_blocked` → 실패, 사유 `"attempted write tool: {name}"`.
3. `expected_tools`: 호출된 도구 집합이 어느 한 경로(안쪽 리스트)라도 부분집합으로
   커버하면 통과. 실패 시 `wrong_tool_calls` 메트릭에 기여.
4. `checks` 순회 — `state` 체크는 이 시점에 adapter로 재조회(§3.1).
5. `judge` 체크가 있거나, 결정적 신호(checks·expected_tools)가 전혀 없으면 LLM-judge
   호출(§5.3). key 없으면 `judge=None`으로 skip하고 결정적 신호만으로 판정. 결정적
   신호도 없고 judge도 불가한 태스크는 `ungraded`로 분리 집계(성공률 분모에서 제외 —
   silent pass 금지).

**success** = 결정적 체크 전부 통과 AND (judge 없음 OR judge pass).

### 4.4 `verifier.task_eval` — 5번째 critic

```python
def task_eval(toolset, adapter, tasks, model_id=None, threshold=0.8,
              write_ok=False, verify_ctx=None) -> Dict[str, Any]
```

- 기존 critic과 동일한 리포트 계약: `{"name": "task_eval", "passed": bool|None, ...}`.
  key/base_url 없으면 `passed=None`(SKIP, 게이트 비차단) — `agent_e2e`와 동일 관례.
- 리포트 필드: `rate`, `threshold`, `results`(태스크별 요약), `metrics` 집계
  (평균 tool_calls / wrong_tool 비율 / 에러율 / skipped_write / ungraded / residue).
- `THRESHOLDS`에 `"task_eval_rate": 0.8` 추가. `run_all`은 시그니처 유지를 위해
  `eval_tasks=None` 키워드 인자를 추가하고, 전달됐을 때만 critic을 실행한다
  (기존 호출부 무영향).

### 4.5 repair 피드백 (`connect._repair` 확장)

`task_eval` 리포트의 실패를 기존 repair 채널에 매핑:

| 실패 신호 | 액션 |
|-----------|------|
| wrong tool (expected 미커버 / read가 write 시도) | 혼동된 도구들에 `enrich(force=True)` — 프롬프트에 실패 태스크 문장과 잘못 선택된 도구명을 컨텍스트로 추가(`llm_repair.enrich`에 선택 인자 `context: str` 추가) |
| 도구 호출 4xx (400/422) | 해당 도구에 `synth_params` 재시도, `source_hint` 대신 실패 args+에러 본문을 힌트로 전달 |
| judge 실패·기타 | 자동 수정 불가 → residual 보고만 (정직) |

connect `--eval` 흐름: 기존 4-critic 루프 **통과 후** task_eval 1회 → 실패 시 위 repair
1회 적용 → task_eval 재실행(총 2회 상한, 별도 라운드 예산). 매 라운드 실행하지 않는 이유:
태스크 실행은 라이브 API + LLM 다회 호출로 기존 critic 대비 수십 배 비싸다.

---

## 5. LLM 사용 설계

### 5.1 모델 해석

`core/registry.resolve(model_id)` 그대로 사용 — 태스크 생성·러너·judge 모두 동일 진입점.
judge용 별도 모델 지정은 `--judge-model` CLI 옵션(기본: 동일 모델).

### 5.2 예산

`llm_repair`의 budget 패턴을 일반화하지 않고(전역 상태 공유 위험), `evals/budget.py`에
동일 구조의 **독립 카운터**를 둔다. 1단위 = eval이 개시한 상호작용 1건: 태스크 생성
호출(≤2), judge 호출(태스크당 ≤2), **태스크 실행 1회**(내부 `run_chat`의 LLM 호출은
`MAX_STEPS=8`로 자체 상한 — 총 LLM 호출 상한 ≈ budget × MAX_STEPS). 소진 시 남은
태스크는 `skipped_budget`으로 집계하고 리포트에서 `infra_errors`와 **별도 필드**로
명시(CI가 "예산 소진"과 "인프라 장애"를 구분) — connect의 LLM-BUDGET exit와 같은 규율.

### 5.3 Judge 프롬프트 계약

입력: 태스크 prompt, 최종 answer, steps 요약(도구명·ok·status만 — args/응답 본문은
2000자 캡). 출력: `{"pass": bool, "reason": "<한 문장>"}` JSON only (`_json_obj` 재사용).
rubric 기본값: "요청을 실제로 완수했고, 답변이 도구 결과에 근거하며, 하지 않은 일을
했다고 주장하지 않는가". 태스크별 `judge.rubric`이 있으면 그것을 사용.

---

## 6. CLI Specification

```
any2agent eval --project <name>
    [--n 8]              생성 시 태스크 수 상한
    [--regen]            evals.json 무시하고 재생성
    [--live-write]       write 태스크 실행 허용 (명시 동의)
    [--model <id>] [--judge-model <id>]
    [--json <path>]      결과 JSON 저장 (CI 아티팩트)
    [--threshold 0.8]
```

- `--live-write`는 인터랙티브면 "대상이 프로덕션이 아님을 확인합니까? [y/N]" 재확인.
- 종료 코드: 게이트 통과 0, 미달 1, 실행 불가(키·base_url 없음) 2 + stderr 사유.
- 출력 형식은 `connect._print_report`와 같은 결:

```
[eval] tasks=8 (read=6 write=2)  model=gpt-4o
  [PASS] notes-read-1   tools=2 checks 3/3
  [FAIL] notes-write-1  wrong tool: picked get__notes, expected notes 생성 경로
  ...
[eval] rate=0.75 (threshold 0.8)  rated=8  wrong_tool=3  errors=1  skipped_write=0  skipped_budget=0  infra=0  ungraded=0
[eval] ❌ below threshold — see <project>.eval-report.json
```

---

## 7. Security & Write-Safety

- [ ] 기본값은 read 태스크만 실행. write는 `--live-write` + 인터랙티브 재확인의 이중 동의.
- [ ] write payload 마커 `[a2a-eval]` 규칙: 태스크 생성 프롬프트에 강제 + grader의 `state`
      체크·cleanup이 마커로 대상 식별. 마커 없는 write 태스크는 로드 시 `invalid` 처리.
- [ ] cleanup 실패는 `residue`로 정직 보고(사용자가 수동 정리할 대상 명시).
- [ ] 인증은 기존 passthrough 원칙 유지 — eval 세션은 env로만 받고(`ANY2AGENT_VERIFY_*`)
      디스크에 저장하지 않는다. `evals.json`·리포트에 쿠키/토큰/응답 본문 원문을 남기지
      않는다(steps 요약만).
- [ ] danger 도구(DELETE)는 cleanup 용도 외에는 write 태스크에서도 제외(생성 프롬프트 금지
      + 로드 검증에서 차단).

---

## 8. Error Handling

| 상황 | 동작 |
|------|------|
| provider key 없음 | 태스크 생성은 결정적 폴백, judge는 skip, 러너는 실행 불가 → critic `passed=None`(SKIP). CLI는 exit 2 |
| base_url 없음 | 실행 불가 — CLI exit 2, connect에서는 task_eval 자체를 건너뜀 |
| 태스크가 참조하는 도구가 toolset에 없음 | `invalid` 집계 + 리포트 명시, 해당 태스크 제외 |
| run_chat LLM 예외 | trace.error 기록, 태스크 실패 처리(에이전트 결함이 아닌 인프라 실패는 `infra_errors`로 분리 집계해 rate 왜곡 방지) |
| judge JSON 파싱 실패 | 1회 재시도 후 `judge=None` — 결정적 체크만으로 판정 |
| eval budget 소진 | 남은 태스크 `skipped_budget`, 리포트에 명시 (silent truncation 금지) |

---

## 9. Test Plan

pytest 도입 (`pyproject.toml`에 `[tool.pytest.ini_options]` + dev extra).

| Type | Target | 방법 |
|------|--------|------|
| Unit | `grader.grade` 전 체크 타입 | 고정 EvalTrace/EvalTask 픽스처, adapter는 스텁 |
| Unit | `tasks.load_or_generate` 폴백·검증·curation 보호 | LLM 미가용 경로, invalid 도구 참조 |
| Unit | `runner` confirm 정책 | run_chat을 흉내내는 제너레이터 스텁으로 read/write/auto_confirm 3분기 |
| Integration | examples/notes-api 전체 사이클 | uvicorn 기동 → eval 실행(무LLM: 결정적 태스크+체크) → rate 산출 확인 |
| Regression | 기존 4 critic | `verifier.run_all(eval_tasks=None)` 기존 동작 불변 확인 |

핵심 케이스:
- [ ] Happy: notes-api read 태스크 2-step 완수 → success
- [ ] Error: read 태스크가 write 시도 → write_blocked 실패 + wrong_tool 메트릭
- [ ] Edge: checks 없고 key 없음 → ungraded 분리 집계, rate 분모 제외

---

## 10. File Structure & Naming

```
any2agent/
├── evals/
│   ├── __init__.py
│   ├── model.py       # EvalTask/EvalTrace/EvalResult (~80 LOC)
│   ├── budget.py      # eval 전용 call budget (~20 LOC)
│   ├── tasks.py       # load_or_generate + 폴백 + 검증 (~150 LOC)
│   ├── runner.py      # run_task + cleanup (~120 LOC)
│   └── grader.py      # grade + judge (~120 LOC)
├── verifier.py        # +task_eval, THRESHOLDS에 task_eval_rate (+~60 LOC)
├── core/agent.py      # ctx auto_confirm 분기 (+~6 LOC)
├── connect.py         # --eval 최종 게이트 + repair 매핑 (+~50 LOC)
├── cli.py             # eval 서브커맨드 (+~40 LOC)
└── config.py          # evals_path() (+~5 LOC)
tests/
├── test_grader.py
├── test_tasks.py
├── test_runner.py
└── test_integration_notes_api.py
```

네이밍은 기존 코드베이스 관례(snake_case 모듈, dataclass, 모듈 docstring에 설계 의도 서술) 준수.

---

## 11. Implementation Order

1. [ ] `evals/model.py` + `config.evals_path()` — 데이터 계약 고정
2. [ ] `evals/tasks.py` 결정적 폴백 경로 + 로드/검증 (LLM 없이 테스트 가능)
3. [ ] `core/agent.py` auto_confirm 분기 + `evals/runner.py` (read 경로 먼저)
4. [ ] `evals/grader.py` 결정적 체크 4종
5. [ ] `verifier.task_eval` + `cli.py eval` — 여기서 무LLM 수직 슬라이스 완성, notes-api 통합 테스트
6. [ ] LLM 태스크 생성 + judge + `evals/budget.py`
7. [ ] write 태스크: `--live-write`, cleanup, 마커 검증
8. [ ] connect `--eval` + repair 피드백(`enrich` context 인자)
9. [ ] pytest 스위트 완성 + README/CHANGELOG 갱신

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-05 | Initial draft | jhchoi |
