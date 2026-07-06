# runtime-telemetry Design Document

> **Summary**: 도구 호출 jsonl 기록 → 도구별 집계 → 드리프트(의심 도구) 판정 → 콘솔 노출
>
> **Project**: any2agent / **Date**: 2026-07-06 / **Status**: Draft
> **Planning Doc**: [runtime-telemetry.plan.md](runtime-telemetry.plan.md)
> Context Anchor: plan과 동일

## 1. Architecture

```
run_chat / confirm_and_run
   └─ dispatch.execute 결과 ──▶ telemetry.record(state_dir, tool, ok, status, ms)
                                   └─ .any2agent-state/<project>/tool-calls.jsonl (로테이션)
GET /evals ──▶ telemetry.summary(state_dir) ──▶ runtime {calls_total, tools[], suspects[]}
evals.html ──▶ "Live usage" 카드 (의심 도구 강조 + 재검증 안내)
chat.html 배지 ──▶ suspects 있으면 ⚠ 병기
```

기록 원칙: **이름·결과·시간만** (tool, ok, status, ms, ts). args/응답 본문/사용자 신원은
절대 기록하지 않는다(개인정보·시크릿 유출 방지 — memory와 동일 규율). 기록 실패는
어떤 경우에도 대화를 깨지 않는다(전 예외 흡수).

## 2. Module — `evals/telemetry.py` (~90 LOC)

- `record(state_dir, tool, ok, status=None, ms=None, authz=False)` — 1줄 append.
  `authz=True`(401/403)는 `ok=False`여도 에러로 세지 않도록 엔트리에 표기.
  파일이 `MAX_LINES(5000)` 초과 시 최근 `KEEP(2500)`줄로 재작성(로테이션).
- `load(state_dir, n=2000)` — tail n, 파손 줄 skip (history.py 관례).
- `summary(state_dir, window=10)` →
  ```json
  {"calls_total": 123,
   "tools": [{"tool":"notes_list","calls":40,"errors":2,"error_rate":0.05,
              "avg_ms":120,"last_ts":...,"recent_errors":0}, ...],
   "suspects": [{"tool":"notes_get","recent_errors":6,"recent_calls":10,
                 "hint":"failing in live use — run `any2agent eval` to re-verify"}]}
  ```
  suspect 판정: 도구별 **최근 window회** 중 비-authz 에러율 ≥ 0.5 AND 해당 표본 ≥ 5.
  판정이 최근 창만 보므로 복구되면 자연 해제 (플래그 저장 없음).

## 3. 기록 지점 (agent.py)

- `run_chat` 도구 분기: `dispatch.execute` 전후 `time.time()`으로 ms 계측 →
  결과가 `confirm_required`면 **기록하지 않음**(실행이 아님), auto_confirm 재실행
  포함 실제 실행 결과만 기록. search_tools·memory 도구는 로컬 연산이라 제외.
- `confirm_and_run`: 동일 계측·기록.
- state_dir는 이미 `ctx["state_dir"]`로 흐름 — 없으면(eval 스텁 등) no-op.
- composite는 dispatch가 하나의 결과를 반환하므로 **composite 이름으로 1건** 기록
  (스텝별 기록은 후속 — 지금은 도구 단위 신호로 충분).

## 4. 노출

- `server/app.py /evals`: `"runtime": telemetry.summary(cfg.state_dir())` 추가
  (요청 시 재읽기 — 기존 관례). 데이터 없으면 `{"calls_total": 0, ...}`.
- `evals.html`: "Live usage" 카드 — 도구별 표(호출수·에러율·평균 ms), suspects는
  상단에 경고 배너("`notes_get` is failing in live use — re-run the check").
  calls_total=0이면 카드 생략.
- `chat.html` 배지: `d.runtime.suspects.length`면 텍스트에 ` ⚠` 병기.

## 5. Error Handling

| 상황 | 동작 |
|------|------|
| record 중 어떤 예외든 | 흡수(no-raise) — 대화 우선 |
| jsonl 파손 줄 | load에서 skip |
| state_dir 미지정 | no-op |
| 401/403 | authz=True로 기록, 에러율 집계에서 제외 |

## 6. Test Plan

- record/load/rotation(5000→2500), 파손 줄, no-raise(쓰기 불가 디렉터리)
- summary: error_rate·avg_ms 계산, suspect 발동(5/10 실패)·미발동(표본<5, authz만)·복구 해제
- run_chat 기록: 스텁 run으로 도구 실행 1건 → 파일 1줄, confirm_required는 미기록
- /evals runtime 섹션 (TestClient)
- 회귀 94 무파손 + 라이브: 채팅 1회 → jsonl 확인, 강제 실패 → 콘솔 suspect

## 7. Implementation Order
1. [ ] telemetry.py + 단위 테스트
2. [ ] agent.py 기록 지점 + 테스트
3. [ ] /evals + UI 카드 + 배지
4. [ ] 라이브 검증 → gap 분석 → 커밋/푸시
