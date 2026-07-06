# tool-composition Design Document (Phase 2)

> **Summary**: 워크플로 합성 도구 — LLM 후보 제안(인터랙티브 승인) + 결정적 다중 스텝 실행기(중간값 바인딩·부분 실패 정직 보고·플래그 MAX 상속)
>
> **Project**: any2agent
> **Author**: jhchoi (CTO)
> **Date**: 2026-07-06
> **Status**: Draft
> **Planning Doc**: [tool-consolidation.plan.md](../../01-plan/features/tool-consolidation.plan.md) (FR-04, FR-05)
> **Phase 1**: [tool-consolidation.design.md](tool-consolidation.design.md) (결정적 셰이핑 — 본 Phase의 전제)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | Anthropic 가이드 "사람이 업무를 나누는 단위로 도구를 설계하라"(`schedule_event`=가용시간 조회+예약 합성). Phase 1 재명명 후에도 `notes_list → 하나 골라 → notes_get`은 여전히 에이전트가 2턴에 나눠 수행 — 컨텍스트·왕복 비용 |
| **WHO** | 생성 에이전트의 LLM(멀티스텝 작업을 1회 호출로 단축), toolspec을 curation하는 개발자(합성 후보를 승인) |
| **RISK** | (1) 위험한/비현실적 체인 자동 채택 → **인터랙티브 승인 강제**로 방지 (2) 부분 실패 상태 불일치 → **정직 보고 + 롤백 안 함 명시** (3) write 합성이 확인 게이트 우회 → **구성 스텝 플래그 MAX 상속**, dispatch 게이트가 합성 도구 자체에 발동 |
| **SUCCESS** | notes-api에서 2-스텝 합성 도구(`notes_list → notes_get`)가 라이브 실행, 부분 실패 시 수행 스텝 정직 보고, 기존 52 테스트 무파손, `eval --compare`로 채택 검증 권고 |
| **SCOPE** | FR-04(제안+승인) + FR-05(실행기). FR-07(마이그레이션 커맨드)은 descoped. 중첩 합성 금지, danger 스텝 금지 |

## 1. 가이드 원칙 → 구현 매핑 (분석 근거)

| Anthropic 가이드 원칙 | Phase 1 이후의 잔여 문제 | Phase 2 대응 |
|---|---|---|
| "사람이 업무를 나누는 단위로 도구를 설계 — `schedule_event`처럼 조회+행동을 합성" | 재명명은 이름만 개선; 워크플로는 여전히 원자 도구 나열 | 합성 backing(`backing.composite`)으로 다중 스텝을 하나의 도구로 노출 |
| "에이전트는 컨텍스트가 비싸다 — 왕복을 줄여라" | list→detail이 최소 2 LLM 턴(중간 목록 전체가 컨텍스트로 유입) | 실행기가 서버측에서 순차 실행, 최종 결과만 반환 → 1 툴콜 |
| "평가로 도구 변경을 검증하라" | Phase 1이 `eval --compare` 확보 | 합성 채택 후 `eval --compare <precompose>` 권고를 출력에 배선(재구현 없음) |
| "위험을 사람이 통제 — 자동화가 안전을 대체하지 않는다" | — | 제안은 LLM, **채택은 사람**(자동 채택용 `--yes` 부재가 곧 안전). danger 스텝 합성 금지 |

## 2. Architecture (선택: dispatch-계층 실행기 + 별도 제안 모듈)

선행 결정(플랜 §6, CTO 인계): **합성 실행은 adapter가 아니라 dispatch 계층** — adapter는 단일 HTTP 호출 유지.

- **A안(adapter 확장)**: RestAdapter가 다중 호출 — adapter의 "단일 호출" 계약 파괴, 전송/오케스트레이션 관심사 혼재. 기각
- **B안(실행기를 dispatch.execute 안에 인라인)**: dispatch가 비대해지고 바인딩 파서·부분실패 리포트가 게이트 로직과 섞임. 기각
- **C안(채택)**: 실행기 `any2agent/core/composite.py`(dispatch가 호출하는 sibling) + 제안/승인 `any2agent/compose.py`(CLI). dispatch는 "합성이면 위임"만 담당.

```
제안(오프라인/개발 시):
  compose.propose(toolset [, history chains])   # LLM 우선, 무키 시 결정적 list→detail 폴백
     └▶ compose.validate_composite (danger 금지·중첩 금지·바인딩 파싱·플래그 MAX)
     └▶ 인터랙티브 승인 (사람) ──▶ toolset.tools.append ──▶ save
                                     └▶ "eval --compare <precompose>" 권고 출력

실행(런타임):
  agent.run_chat / eval runner ──▶ dispatch.execute(spec, args, adapter, ctx, confirmed, toolset)
     │ composite.is_composite(spec)?
     │   yes ─▶ effective_flags = MAX(step flags)   # 게이트 결정에 사용(저장값 아닌 스텝에서 재계산)
     │         write/danger & not confirmed ─▶ confirm_required (합성 도구 이름으로 카드)
     │         confirmed ─▶ composite.run(...)  # 순차 실행·바인딩·부분실패 정직 보고
     │   no  ─▶ 기존 write/danger 게이트 ─▶ adapter.call
```

핵심: **ToolSpec 스키마 변경 없음.** 합성 정보는 이미 free-form인 `backing`에 `composite` 키로 실린다. `write`/`danger` 필드는 채택 시 MAX로 기록되지만, dispatch는 저장값을 신뢰하지 않고 스텝에서 **재계산**해 게이트를 건다(수동 편집 오류에도 안전).

## 3. Data Model — 합성 backing 스키마 + 바인딩 문법

### 3.1 backing.composite (직렬화 계약)

```json
{
  "name": "notes_open_first",
  "description": "List notes then fetch the first note's detail in one step.",
  "parameters": {"type": "object", "properties": {
      "limit": {"type": "integer", "description": "How many to list before picking the first."}
  }},
  "backing": {"composite": [
      {"tool": "notes_list", "args": {"limit": "$input.limit"}},
      {"tool": "notes_get",  "args": {"note_id": "$steps[0].data[0].id"}}
  ]},
  "write": false, "danger": false, "domain": "notes"
}
```

- `backing.composite`: **2개 이상**의 스텝 리스트. 각 스텝 `{"tool": <canonical or alias>, "args": {<param>: <literal | binding>}}`.
- 구성 도구는 canonical 이름 또는 alias로 참조 — `ToolSet.by_name()`이 해석(Phase 1 하위호환 계승).
- 합성 도구의 `parameters`는 **호출자(LLM)가 채우는 입력**만 선언 — 바인딩으로 채워지는 스텝 인자는 여기 넣지 않는다.

### 3.2 바인딩 문법 (결정적, 무LLM)

값이 문자열이고 `$`로 시작하면 **바인딩 표현식**, 아니면 리터럴. 두 루트:

| 루트 | 의미 |
|------|------|
| `$input` | 합성 도구가 호출될 때 받은 입력 args (dict) |
| `$steps[i]` | i번째(0-기반) 스텝의 **실행 결과 dict** `{ok,status,data,error}`. `i` 음수 허용(`-1`=직전) |

루트 뒤에 **경로**가 붙는다: `.key`(dict 키), `[n]`(list 인덱스, 음수 허용). 체이닝 가능.

```
$input.note_id            → 입력 args의 note_id
$steps[0].data            → 0번 스텝 결과의 data
$steps[0].data[0].id      → 0번 스텝 data(list)의 첫 원소의 id
$steps[-1].data.items[2]  → 직전 스텝 data.items의 3번째
```

바인딩은 args 구조 내 **중첩된 문자열에도** 적용(dict/list 재귀 해석). 해석 실패(키 없음·인덱스 초과·타입 불일치)는 **`BindingError`** → 해당 스텝을 바인딩 실패로 기록하고 **실행 중단**(뒤 스텝 미실행). 조용한 `None` 대입 금지(honest report).

## 4. Module Specification

### 4.1 `any2agent/core/composite.py` (실행기, ~110 LOC)

| 심볼 | 계약 |
|------|------|
| `is_composite(spec) -> bool` | `isinstance(spec.backing.get("composite"), list) and len>=1`. dispatch·verifier가 사용하는 정본 판정 |
| `effective_flags(spec, by_name) -> (write, danger)` | 구성 스텝 플래그의 OR(=MAX). 미해석 스텝은 무시. **게이트 결정의 근거** |
| `resolve_args(args, input_args, results) -> args` | 바인딩 재귀 해석. 실패 시 `BindingError` |
| `run(spec, input_args, adapter, ctx, confirmed, by_name) -> dict` | 순차 실행기(§4.2 결과 계약) |
| `BindingError(Exception)` | 바인딩 해석 실패 |

`run`은 스텝마다 `adapter.call(step_spec, resolved_args, ctx)`를 직접 호출(합성 게이트는 dispatch에서 이미 통과). 스텝이 다시 합성이면 즉시 실패(중첩 금지).

### 4.2 실행 결과 계약 (step results / partial failure shape)

성공(전 스텝 2xx):
```json
{"ok": true, "composite": "notes_open_first",
 "steps": [{"tool":"notes_list","args":{...},"ok":true,"status":200,"write":false,"error":null},
           {"tool":"notes_get","args":{"note_id":1},"ok":true,"status":200,"write":false,"error":null}],
 "completed": 2, "total": 2, "data": <마지막 스텝의 data>, "error": null}
```

부분 실패(스텝 k에서 중단):
```json
{"ok": false, "composite": "notes_open_first",
 "steps": [<0..k 기록, k는 ok:false>],
 "completed": <성공 스텝 수>, "total": <총 스텝 수>,
 "failed_step": k, "failed_tool": "notes_get",
 "error": "http_404" | "binding_error: ..." | "unknown_tool: ..." | "nested composites are not allowed",
 "rolled_back": false,
 "note": "이미 실행된 스텝은 롤백되지 않습니다" (+ 완료 스텝 중 write가 있으면 side-effect 경고)}
```

- `data`는 편의 필드(마지막 성공 스텝의 `data`) — 성공 시에만 의미.
- `rolled_back: false`는 **항상 명시** — 합성은 트랜잭션이 아님을 계약으로 못박음.
- 완료된 스텝에 write가 포함된 채 뒤 스텝이 실패하면 `note`에 "N write step(s) already applied — not rolled back" 경고(FR-05 정직 보고).

### 4.3 `any2agent/compose.py` (제안 + 승인, ~140 LOC)

| 심볼 | 계약 |
|------|------|
| `read_history_chains(state_dir) -> List[(chain, count)]` | eval-history.jsonl의 `chains`(멀티스텝 툴 시퀀스) 빈도 집계. 없으면 `[]`(best-effort) |
| `propose(toolset, chains=None, n=6, model_id=None) -> (proposals, rejected)` | LLM 제안 우선; 무키 시 결정적 list→detail 폴백. 각 후보 검증 통과분만 `proposals`, 사유와 함께 `rejected` |
| `validate_composite(cand, toolset) -> (ok, reason)` | §7 규칙 |
| `approve_interactive(proposals, toolset, dry_run, in_fn, out) -> adopted` | 후보별 출력 후 y/N. 승인 시 append. **dry_run은 절대 미기록** |
| `run_compose(args)` | CLI 엔트리 — config/toolset 로드 → propose → approve → save → `eval --compare` 권고 |

무LLM 결정적 폴백(house style: graceful no-key degradation): read 도구 중 `is_list_tool(A)`이고 `B`의 path가 `A`의 path를 `/{var}` 로 확장하는 (A,B) 쌍에 대해 `A → B(note_id=$steps[0].data[0].id)` 합성을 제안. history chains가 있으면 그 쌍을 우선. 후보는 `source: "llm" | "chain" | "pair"`로 표시(정직).

## 5. LLM 제안 프롬프트 (FR-04)

`registry.completion` 1회 호출(루프 없음 → 별도 budget 불필요). 입력: 비-danger 도구 카탈로그 + (있으면) 빈발 체인. 출력: 엄격 JSON 배열. 프롬프트에 §3.2 바인딩 문법과 "danger 도구 사용 금지·2스텝 이상·write는 반드시 마지막" 규칙 명시. 파싱 실패/무키는 폴백. mock LLM은 compose 프롬프트를 이해하지 못하므로 **제안 테스트는 `_llm_propose` 몽키패치 스텁**으로 수행하고, 실행기는 notes-api 라이브로 검증(무LLM 경로).

## 6. 승인 UX (Approval)

```
[compose] 1 candidate (source=pair):

  notes_open_first  (read; 2 steps)
    List notes then fetch the first note's detail in one step.
    step 1: notes_list        args {"limit": "$input.limit"}
    step 2: notes_get         args {"note_id": "$steps[0].data[0].id"}
  Adopt this composite tool? [y/N]:
```

- write 포함 시 헤더에 `(write; N steps)` + "⚠ 확인 게이트가 이 도구에도 적용됩니다" 고지.
- 승인 → toolset append, 채택 전 toolspec을 `<project>.toolspec.precompose.json`으로 백업.
- 종료 시: `[compose] adopted 1 — verify with: any2agent eval --compare <project>.toolspec.precompose.json`.
- `--dry-run`: 후보만 출력, 프롬프트·기록·백업 없음.
- **자동 채택용 `--yes` 없음** — 승인이 안전 경계.

## 7. Validation (합성도 일반 도구처럼 검증 + 합성 고유 규칙)

`validate_composite`가 채택 전, 그리고 `verifier.accuracy`가 로드 시 적용하는 규칙:

| 규칙 | 위반 시 |
|------|---------|
| `composite`는 2개 이상 스텝 | reject "needs >= 2 steps" |
| 각 스텝 tool이 `by_name`으로 해석됨 | reject "unknown tool: X" |
| 스텝에 danger 도구 없음 (FR-04) | reject "danger tool not allowed: X" |
| 스텝이 다시 합성 아님 (중첩 금지) | reject "nested composite: X" |
| 모든 `args`의 바인딩이 파싱됨 | reject "bad binding: ..." |
| 이름이 기존 도구와 충돌 안 함 | reject "name already exists" |
| `write`/`danger` 필드 == effective_flags(MAX) | 채택 시 강제 설정(정합) |

## 8. 호환성 터치포인트 (기존 코드 경로 적응)

| 경로 | 현재 동작 | 적응 |
|------|-----------|------|
| `core/dispatch.execute` | `(spec,args,adapter,ctx,confirmed)` | `toolset=None` 인자 추가; 합성 감지→위임, 게이트는 effective_flags로 |
| `agent.run_chat` / `confirm_and_run` | dispatch 호출 | `toolset=toolset` 전달(스코프에 이미 있음) |
| `evals/runner.run_cleanup` | dispatch 호출 | `toolset=toolset` 전달 |
| `verifier.accuracy(toolset)` | method/path 없으면 bad | 합성이면 §7 구조 검증(정상이면 통과) |
| `verifier.liveness` | read 도구 스모크콜(adapter 직접) | 합성은 **스킵**(unprobed "composite") — 다중콜이라 스모크 부적합·write 유발 위험 |
| `verifier.coverage` | (method,path) 매칭 | 합성은 `("","")` → 라우트 미매치(무해). 변경 없음 |
| `verifier.agent_e2e` | `to_function()` 광고 | 합성도 선택 대상(의도된 노출). 변경 없음 |
| `shape.is_list_tool` | `_action(method,path)` | 합성이면 `False`(빈 backing이 list로 오판되어 limit 붙는 것 방지) — 인라인 가드 |
| `toolrag.score/build_seed` | name/desc/domain/params | 합성 필드 정상. 변경 없음 |
| `evals/tasks.validate` | 참조 도구 by_name 확인 | 합성 이름도 by_name 해석 → 통과. 변경 없음 |
| `evals/grader` metrics | `called`(순서 O) 기록 | `chain` 키 추가(순서 있는 툴콜) — FR-04 체인 마이닝 근거(순수 additive) |
| `evals/history.append` | 요약 라인 | 멀티스텝 `chains` 기록(있을 때만, 상한) — 순수 additive |

## 9. Error Handling

| 상황 | 동작 |
|------|------|
| 합성 스텝 tool이 toolset에 없음 | 실행: `unknown_tool` 실패·중단 / 검증: reject |
| 바인딩 경로 해석 실패(키·인덱스·타입) | `BindingError` → 해당 스텝 실패·중단, `error="binding_error: ..."` |
| 스텝 k가 HTTP 4xx/5xx/전송실패 | 스텝 k `ok:false` 기록, 뒤 스텝 미실행, `failed_step=k`, 롤백 안 함 명시 |
| write 스텝 성공 후 뒤 스텝 실패 | 위 + `note`에 "이미 적용된 write N개 — 롤백 안 됨" 경고 |
| 합성 도구가 write/danger인데 미확인 호출 | dispatch가 `confirm_required` 반환(합성 도구 이름으로 카드) |
| dispatch에 toolset 미전달인데 합성 호출 | `{"ok":false,"error":"composite requires a toolset to resolve steps"}` (조용한 성공 금지) |
| 중첩 합성(스텝이 합성) | 실행·검증 모두 거부 |
| 제안 LLM 무키/파싱 실패 | 결정적 폴백; 그것도 없으면 "no candidates" 정직 출력 |
| dry-run | 어떤 파일도 수정·백업하지 않음 |

## 10. Test Plan (`tests/test_composite.py` + compose 제안)

**바인딩 해석** — `$input.x`, `$steps[i].data[j].k`, 음수 인덱스, 중첩 dict/list, 리터럴 통과, 각 실패 종류가 `BindingError`.
**실행기(스텁 adapter)** — 2스텝 성공(결과 계약·`data`=마지막), 스텝2 실패 시 부분 보고(`completed`/`failed_step`/`rolled_back:false`), write 스텝 후 실패 시 side-effect note, unknown/중첩 스텝 거부, 바인딩으로 스텝1 출력→스텝2 인자 전달 검증(스텁 calls 기록).
**플래그·게이트** — `effective_flags` MAX(read+write→write, read+read→read); write 합성이 `confirmed=False`에서 `confirm_required`, `confirmed=True`에서 실행; dispatch에 toolset 미전달 시 정직 에러.
**검증** — danger 스텝·중첩·1스텝·미지 도구·바인딩 오류·이름 충돌 각각 reject; 정상 합성 accept.
**compose 제안** — `_llm_propose` 스텁으로 후보→검증→반영; 무키 결정적 list→detail 폴백이 notes-api 쌍 생성; `read_history_chains`가 chains 라인 집계.
**호환성** — `verifier.accuracy`가 합성 포함 toolset에 bad 없이 통과; `verifier.liveness`가 합성 스킵; `tasks.validate`가 합성 이름 참조 통과; `shape.apply`가 합성에 limit 안 붙임.
**CLI** — `compose --dry-run`이 toolspec 미변경; 승인(in_fn 스텁)이 append + precompose 백업 + compare 권고 출력.
**회귀** — 기존 52 전부 통과.

## 11. Implementation Order

1. [ ] `core/composite.py`: `is_composite`·`BindingError`·`resolve_args`·`effective_flags`·`run`
2. [ ] `core/dispatch.py`: `toolset` 인자 + 합성 위임/게이트; 호출자 3곳 전달
3. [ ] 호환성: `verifier`(accuracy/liveness)·`shape.is_list_tool`·`grader.chain`·`history.chains`
4. [ ] `compose.py`: propose/validate/approve + `read_history_chains` + `run_compose`
5. [ ] `cli.py`: `compose` 서브커맨드(`--dry-run`, `--n`, `--model`)
6. [ ] `tests/test_composite.py` (+ 제안/CLI 스텁 테스트) — 52+ green
7. [ ] notes-api 라이브 실행기 검증(list→get) + 문서/리포트
