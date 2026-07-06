# response-shaping Design Document

> **Summary**: LLM-facing 도구 응답 렌더 레이어 — 구조 인지 절단, concise/detailed, actionable 에러 힌트
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft
> **Planning Doc**: [response-shaping.plan.md](response-shaping.plan.md)

## Context Anchor

Plan 문서와 동일 (WHY: raw JSON+무단 절단+무의미 에러 / SUCCESS: 유효 JSON+유도 문구+힌트 / SCOPE: respond.py+agent 통합+shape 승격).

## 1. Architecture — 렌더 레이어의 위치

```
adapter.call ──▶ result{ok,status,data,error}  ← 원본, 불변
   │                        │
   │ (grader state check,   │ run_chat 이벤트(UI)·trace: 원본 그대로
   │  verifier liveness는   ▼
   │  원본 소비)      agent._tool_msg ──▶ respond.render(result, spec, toolset, fmt)
   │                                        │  ok:  shape(data)  → 절단+_meta+유도
   │                                        │  err: explain(status,error) → hint 부착
   │                                        ▼
   │                                  LLM tool message (항상 유효 JSON, ≤ cap)
```

**핵심 불변식**: respond는 `_tool_msg`(LLM 메시지) 전용. adapter 반환값, SSE 이벤트,
EvalTrace.steps, grader의 state 재조회는 원본을 본다 — 셰이핑이 채점·UI를 오염시키지 않는다.

## 2. Module Specification — `any2agent/respond.py` (~140 LOC)

### 2.1 `shape(data, mode="concise", max_items=None, max_str=500) -> (shaped, notes)`

| mode | max_items 기본 | 동작 |
|---|---|---|
| `concise` | 10 | 배열 절단 + null/빈 문자열/빈 컬렉션 필드 제거 + 긴 문자열 `…[truncated]` 마커 절단 |
| `detailed` | 50 | 배열 절단 + 긴 문자열 마커 절단 (필드는 전부 보존 — 후속 호출용 ID 유지, 가이드 원칙) |

반환: `(shaped, notes, truncations)` — truncations는 `[{"shown","total"}]`로 render가
`_meta.truncated`에 실어 프로그램적 접근을 보장.

- 재귀 적용 (중첩 배열/객체). 절단 발생 시 notes에
  `"list truncated to 10 of 137 items — refine with filters or a smaller limit"` 추가.
- 딕셔너리 최상위가 배열을 감싼 형태(`{"items": [...], "total": N}`)도 내부 배열에 동일 적용.

### 2.2 `render(result, spec, toolset, response_format=None, cap=6000) -> str`

1. `result.ok`이면 `data`를 shape. 직렬화가 cap 초과 시 **max_items를 절반씩 축소**
   (10→5→2→1)하며 재시도, 최후엔 data를 `{"_meta": {"omitted": true, hint}}`로 대체 —
   **어떤 경우에도 유효 JSON** (FR-02).
2. 셰이핑 발생 시 `data._meta = {"truncated": {...}, "hint": "..."}` (배열이면 wrapper
   `{"items": [...], "_meta": ...}`로 승격).
3. `result.ok == False`면 §2.3의 hint를 `result["hint"]`로 부착.
4. 반환: `json.dumps(...)` — `_tool_msg`가 그대로 사용, 문자 슬라이싱 제거.

### 2.3 `explain(result, spec, toolset) -> str` — 에러 힌트 표

| 신호 | 힌트 (결정적 템플릿) |
|---|---|
| 400/422 | "The arguments were rejected — re-check required parameters and types against this tool's schema.{body의 detail 400자}" |
| 401/403 | "Not permitted for this user's session (RBAC). Do not retry with different arguments; tell the user instead." |
| 404 + spec에 path param | "Resource not found — the identifier may be wrong or stale.{형제 제안}" |
| 404 형제 제안 | spec.name이 `<res>_get/update/replace/delete/…`이고 toolset에 `<res>_list` 또는 `<res>_search`가 있으면: " Call {sibling} first to find a valid id." |
| 405 | "Method not allowed — this operation may not exist on the target; try a different tool." |
| 429 | "Rate limited — wait before retrying, and prefer narrower queries." |
| 5xx | "The target API failed internally — retry once; if it persists, report the failure to the user." |
| transport(예외 문자열) | "Could not reach the target API — it may be down or the base URL wrong. Do not retry repeatedly." |
| `unknown_tool` (로컬) | "No such tool — pick one from the provided tool list, or call search_tools." (transport 힌트 금지; 기타 snake_case 로컬 코드는 힌트 없음) |

형제 탐색은 결정적: 이름 `rsplit("_", 1)` 접두사 일치 + 접미사 `list|search`. 셰이핑 안 된
(기계식 이름) toolset에서는 자연히 미발동 — 잘못된 유도 없음 (Plan risk 대응).

## 3. `response_format` 파라미터 (FR-03/04)

- **스키마 승격** (`shape.py` pass 2 확장): list 도구(`is_list_tool`)에
  `response_format: {"type":"string","enum":["concise","detailed"],"description":"concise (default) returns trimmed items; detailed keeps all fields for follow-up calls."}` — 기존에 없을 때만.
- **런타임 pop** (`agent.py` 루프): dispatch 호출 **전에** `args.pop("response_format", None)` →
  render에 전달. confirm 재진입(`confirm_and_run`)도 동일. **백엔드로 절대 전달 안 됨.**
- eval runner는 run_chat 이벤트를 소비하므로 자동 적용 — trace에는 pop된 args가 기록됨(무해).

## 4. Integration Diff

| File | Change | LOC |
|------|--------|----|
| `respond.py` (신규) | shape/render/explain | ~140 |
| `core/agent.py` | `_tool_msg(idx,name,result)` → `(..., spec, toolset, fmt)`; args pop; 문자 캡 제거 | ~10 |
| `shape.py` | pass 2에서 response_format 승격 (promoted 집계 포함) | +6 |
| `SHAPING_VERSION` | 1→2 (재실행 시 기존 toolspec도 승격 받도록 apply가 version<2 재실행, 재명명은 이미 alias로 멱등) | +4 |

주의: SHAPING_VERSION 상향 시 `apply`의 noop 가드가 version≥2로 바뀌고, 재명명 파트는
이름이 이미 정식이면 `_MECHANICAL` 불일치로 자동 skip — 이중 실행 안전. 재실행 시
자기 자신이 만든 이름(meta.shaping.renamed 키)은 skipped 잡음으로 보고하지 않고,
renamed 맵은 병합 carry-forward로 audit trail을 보존한다. 에러 본문도 render에서
`_fit`으로 동일 cap 규율 적용.

## 5. Error Handling

| 상황 | 동작 |
|------|------|
| data가 직렬화 불가 객체 | `default=str`로 직렬화 (기존 grader와 동일 관례) |
| 최소 절단으로도 cap 초과 (초대형 단일 객체) | data 생략 + `_meta.omitted` + 유도 문구 — 여전히 유효 JSON |
| spec/toolset 미전달 (방어) | 힌트 생략, 셰이핑만 수행 |
| unknown_tool 등 로컬 에러 | 기존 dict 그대로 + 해당 시 힌트 |

## 6. Test Plan

- shape: 배열 절단·notes, concise null 제거, detailed 필드 보존, 중첩, 문자열 마커, wrapper 형태
- render: cap 초과 시 점진 축소 → 항상 `json.loads` 가능, 최후 omitted 경로
- explain: 상태 클래스 전부 + 404 형제 제안 발동/미발동(비셰이핑 이름)
- agent: response_format pop이 dispatch args에서 빠지는지, `_tool_msg` 산출이 유효 JSON인지
- shape.py: response_format 승격 + version 2 멱등
- 회귀: 기존 74 무파손, notes-api 라이브 스모크

## 7. Implementation Order

1. [ ] respond.py (shape/explain/render) + 단위 테스트
2. [ ] agent.py 통합 (pop + _tool_msg) + shape.py 승격/버전
3. [ ] 전체 테스트 + notes-api 라이브 확인
4. [ ] gap 분석 → 수정 → 커밋/푸시
