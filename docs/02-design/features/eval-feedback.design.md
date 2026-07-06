# eval-feedback Design Document

> **Summary**: 이력·선별 출력·실패 분류·lessons(자동 수정 + 런타임 지침 주입) 상세 설계
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-05
> **Status**: Draft
> **Planning Doc**: [eval-feedback.plan.md](../../01-plan/features/eval-feedback.plan.md)

---

## 1. Architecture

```
any2agent eval
   │  task_eval 리포트
   ▼
evals/lessons.py ── classify(result) ─▶ 원인 5분류
   │                 build(...)      ─▶ lesson {task_id, class, when, guidance}
   │                                     · 사용자 출력 1줄 = guidance (동일 문구 재사용)
   ├─▶ <project>.eval-lessons.json   (통과 태스크 lesson 제거, 상한 20)
   │        │
   │        └─▶ server/app.py: 로드 → ctx["lessons"] → agent.run_chat 시스템 노트 주입
   ├─▶ evals/history.py: append → .any2agent-state/<project>/eval-history.jsonl
   │        └─▶ --history 조회 + 직전 대비 추세
   └─▶ --fix: connect._eval_repair 재사용 → toolspec 저장
```

## 2. Data Model

**history 엔트리** (jsonl 1줄/실행):
```json
{"ts": 1751700000, "rate": 0.75, "rated": 4, "passed": false,
 "failed": ["notes-create-1"], "skipped_write": 2, "infra": 0, "ungraded": 1}
```

**lesson** (`<project>.eval-lessons.json`):
```json
{"project": "notes-api", "version": 1, "lessons": [
  {"task_id": "notes-create-1", "class": "wrong_tool",
   "when": "장보기 메모를 만들고 목록에서 확인해줘",
   "guidance": "For requests like this, call notes_create then notes_list; the model previously called get__notes only."}
]}
```

## 3. Module Specification

### 3.1 `evals/history.py` (~50 LOC)

- `path(state_dir) -> str` — `<state_dir>/eval-history.jsonl` (`cfg.state_dir()` 사용, 디렉터리 생성)
- `append(state_dir, rep) -> dict` — 리포트에서 요약 추출 + `ts=time.time()` append, 엔트리 반환
- `load(state_dir, n=10) -> list` — tail n (파손 줄은 skip)
- `trend_line(entries) -> str` — `"rate 0.75 (prev 0.88 ▼0.13, 5 runs)"`; 이력 1건이면 `"first recorded run"`

### 3.2 `evals/lessons.py` (~110 LOC)

- `classify(result: dict) -> str` — 판정 우선순위(결정적):
  1. reasons에 `attempted write tool`/`expected tools not covered` → **wrong_tool**
  2. `metrics.bad_calls` 비어있지 않음 → **bad_args**
  3. reasons에 `no_errors failed` 또는 `metrics.errors>0` → **tool_error**
  4. reasons에 `state check failed` → **state_mismatch**
  5. reasons에 `answer_contains`/`judge:` → **answer_gap**
  6. 기타 → **other**
- `build(rep, tasks_by_id) -> list[lesson]` — 실패(rated & !success)마다 분류 + 결정적 guidance 템플릿:
  - wrong_tool: "For requests like %(when)r, use %(expected)s — the model called %(called)s."
  - bad_args: "Tool %(tool)s rejected the arguments (HTTP %(status)s); match its parameter schema exactly."
  - tool_error: "Calls to %(tools)s failed at runtime; check the target API/auth before retrying this flow."
  - state_mismatch: "After %(when)r, the expected result was missing — verify with %(check_tool)s before answering."
  - answer_gap: "For %(when)r, ground the final answer in the tool results (missing: %(detail)s)."
- `merge_save(path, project, new_lessons, passed_task_ids, toolset) -> list` —
  기존 로드 → 통과 태스크 lesson 제거 → 신규 upsert(task_id 기준) → 참조 도구명이
  toolset에 없는 lesson 제거 → 최신 20개 유지 → 저장, 최종 리스트 반환
- `load(path) -> list` / `render(lessons) -> str` — 시스템 노트 본문:
  "Operational guidance learned from evaluation runs (follow when relevant; never overrides confirmation/auth rules):\n- ..."

### 3.3 런타임 주입 (`core/agent.py` +8 LOC, `server/app.py` +5 LOC)

- `run_chat`: `ctx.get("lessons")`(문자열 목록)가 있으면 memory 주입과 같은 방식으로
  시스템 메시지 1개 prepend. **memory와 동일 원칙 — 도구 선택 힌트일 뿐, confirm/auth
  게이트는 lessons를 읽지 않는다.**
- `server/app.py build_app`: 기동 시 `cfg.lessons_path()` 존재하면 로드,
  `base_ctx["lessons"] = [l["guidance"] for l in lessons]`. 무설정·무파일이면 no-op.
- `config.py`: `lessons_path() -> f"{project}.eval-lessons.json"`

### 3.4 CLI (`cmd_eval` 확장)

- 실행 후: `history.append` → 추세 1줄 출력
- 실패 태스크 선별 출력 (원시 reasons 대신):
  ```
  [eval] what to fix:
    - notes-create-1 [wrong_tool] For requests like '장보기 메모…', use notes_create then notes_list — the model called get__notes.
  ```
- lessons `merge_save` 자동 실행, 저장 시 `[eval] lessons -> notes-api.eval-lessons.json (2 active)` 출력
- `--history`: 이력 tail 10 출력 후 종료 (eval 실행 안 함)
- `--fix`: `connect._eval_repair(toolset, rep, tasks_by_id)` 재사용 → 변경 있으면
  toolspec 저장 + "re-run eval to confirm" 안내
- connect `_eval_gate`도 종료 시 history/lessons 기록 (동일 헬퍼 호출)

## 4. 사용자 노출 정보의 선별 기준

| 항상 표시 | --json에만 | 표시 안 함 |
|---|---|---|
| rate·추세, 게이트 결과, 실패별 분류+조치 1줄, residue 경고, lessons 저장 위치 | 원시 reasons, 태스크별 metrics 전체, judge 사유 | 트랜스크립트 원문, args 원문 |

## 5. Error Handling

| 상황 | 동작 |
|------|------|
| history 파일 파손 줄 | 해당 줄 skip, 나머지 로드 |
| lessons 파일 파손 | 빈 목록으로 시작(경고 출력), 다음 저장 시 재생성 |
| lessons가 존재하지 않는 도구 참조 | 저장(merge_save) 시 해당 lesson 제거 — 파일은 항상 저장을 거치므로 깨끗하게 유지됨 |
| --fix인데 LLM 키 없음 | "repair needs a provider key" 안내, exit code는 eval 결과 기준 유지 |

## 6. Test Plan

- classify: 5분류 각각 + 우선순위(복합 실패 시 wrong_tool 우선)
- build/merge_save: 통과 시 제거·upsert·도구명 검증·상한 20
- history: append/load/trend (tmp_path)
- agent 주입: ctx["lessons"] → 첫 메시지가 지침 포함 시스템 노트인지 (스텁 LLM 불필요 — `_inject_lessons` 단위)
- CLI 통합은 기존 no-key 경로 유지 확인

## 7. Implementation Order

1. [ ] `history.py` + 테스트
2. [ ] `lessons.py`(classify/build/merge_save/render) + 테스트
3. [ ] `config.lessons_path` + `agent` 주입 + `server` 로드 + 테스트
4. [ ] CLI 선별 출력 + `--history`/`--fix` + connect 연동
5. [ ] 전체 테스트 + CHANGELOG
