# tool-consolidation Design Document (Phase 1)

> **Summary**: 결정적 도구 셰이핑 — 리소스 기반 재명명 + alias 호환 + list→search 승격 + eval --compare
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft
> **Planning Doc**: [tool-consolidation.plan.md](../../01-plan/features/tool-consolidation.plan.md)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 스캐너가 1 라우트=1 도구를 기계 변환(`get__notes`) — Anthropic 가이드 "엔드포인트를 그대로 래핑하지 말라" 위반 |
| **WHO** | 생성된 에이전트의 LLM(도구 선택 정확도), toolspec을 curation하는 개발자 |
| **RISK** | 이름 변경이 기존 toolspec/evals/lessons 참조를 깨뜨림 → alias 해석 레이어로 방지 |
| **SUCCESS** | notes-api에서 `notes_list` 형태 생성, 기존 이름 참조 무파손, eval --compare 동작 |
| **SCOPE** | Phase 1 결정적 셰이핑만 — composite은 A/B 검증 후 Phase 2 |

## 1. 가이드 원칙 → 구현 매핑 (분석 근거)

| Anthropic 가이드 원칙 | 현재 코드의 위반 지점 | Phase 1 대응 |
|---|---|---|
| "명확한 네임스페이싱 — 리소스 접두사 그룹핑" | `code.py:93-100` `_name()`: `<method>_<path>` 기계 변환, 선행 `/`로 `get__notes` 이중 언더스코어. 리소스가 이름 뒤쪽에 묻힘 | `shape.py` 재명명: `<resource>_<action>` — 같은 리소스 도구가 알파벳순으로 모임 |
| "unambiguous한 이름 — `user` 아닌 `user_id`" | path param이 그대로라 무해하나, 도구명 자체가 HTTP 동사 중심이라 의도 불명확 | action 어휘를 CRUD 의미로: `list/get/create/update/replace/delete` |
| "`list_contacts` 말고 `search_contacts` — 에이전트는 컨텍스트가 비싸다" | 컬렉션 GET이 파라미터 0개로 노출 — 전체 목록이 그대로 컨텍스트로 유입 | list 도구에 `limit` 승격 + 설명에 "prefer filters over fetching everything" |
| "도구 수가 많을 때 선택 정확도" | `toolrag.py:43-52` 키워드 매칭 — `get__notes`는 "show my notes"와 토큰 겹침이 약함 | 리소스 명명 자체가 검색 히트를 개선 (`notes` 토큰이 이름 선두에) |
| "평가로 도구 변경을 검증하라" | 측정 수단 없었음 → 이제 eval 하네스 있음 | `eval --compare`: 전/후 toolset을 동일 태스크로 A/B |

## 2. Architecture (선택: Pragmatic Balance)

- **A안(스캐너 수정)**: `_name()`을 직접 고침 — alias 없이는 기존 아티팩트 전부 파손, OpenAPI 경로와 이원화. 기각
- **B안(독립 shape 패키지 + 마이그레이션 CLI)**: 과설계 — Phase 1 규모에 마이그레이션 커맨드는 불필요(alias가 해결)
- **C안(채택)**: `any2agent/shape.py` 단일 모듈 — toolset을 in-place 변형하고 통계 dict를
  반환하는 패스(§4.3 계약이 정본) + `spec.py` alias 지원

```
connect:  scan ──▶ shape.apply(toolset)  ──▶ verify → repair → (--eval)
                    │ 재명명(+aliases)
                    │ list→search 승격
                    └ meta.shaping 기록(멱등)
runtime:  by_name() 이 alias도 해석 → 기존 evals/lessons/dispatch 참조 무파손
compare:  eval --compare old.toolspec.json → 동일 태스크 2회 실행 → 비교 리포트
```

## 3. Data Model

### 3.1 `ToolSpec.aliases: List[str]` (spec.py)

- 직렬화에 포함, `from_dict` 하위 호환(없으면 `[]`)
- `ToolSet.by_name()`: 정식 이름 우선 등록 후 alias를 **비충돌 시에만** 추가 등록
- `to_function()`은 정식 이름만 노출 (LLM에는 새 이름만 보임)

### 3.2 `meta.shaping` (멱등성)

```json
{"shaping": {"version": 1, "renamed": {"notes_list": "get__notes", ...}}}
```
`shape.apply`는 `meta.shaping.version >= 1`이면 no-op (재실행 안전).

## 4. Module Specification — `any2agent/shape.py` (~150 LOC)

### 4.1 재명명 규칙 (결정적)

path를 세그먼트로 분해해 리소스와 수식어를 추출:

```
/notes            → resource=notes
/notes/{id}       → resource=notes, by-id
/users/{id}/posts → resource=users_posts (중첩: 정적 세그먼트 연결)
/health           → resource=health
```

action 결정 표:

| method | path 끝이 `{var}` | action |
|---|---|---|
| GET/HEAD | no | `list` (컬렉션 — 마지막 정적 세그먼트가 복수형이면 컬렉션으로 판정) / 단일 정적 세그먼트 + 비복수형이면 싱글턴 `get` (`/health`→`health_get`, `/notes`→`notes_list`) |
| GET/HEAD | yes | `get` |
| POST | no | `create` |
| POST | yes | `update` (RPC성 POST) |
| PUT | * | `replace` |
| PATCH | * | `update` |
| DELETE | * | `delete` |

이름 = `<resource>_<action>` (snake_case 정리, 60자 캡). **보수적 폴백**: 리소스 추출
불가(빈 path·특수문자만), 생성된 이름이 기존/신규 이름과 충돌, 또는 이미 사람이 고친
흔적(기계 변환 패턴 `^(get|post|put|patch|delete|head|options)_`과 불일치)이면 **기존
이름 유지 + alias 없음**. 기존 이름은 항상 `aliases`에 보존.

### 4.2 list→search 승격 (결정적)

대상: `action == list`인 read 도구. 변경:
- `parameters.properties.limit = {"type": "integer", "description": "Max items to return (default 20). Use the smallest limit that answers the question."}` — 기존에 없을 때만
- description 말미에 붙임: `" Prefer filters/limit over fetching everything — results can be large."`
- 백엔드가 limit을 무시해도 무해(쿼리스트링 잉여 파라미터) — RestAdapter가 그대로 전달

LLM 기반 필터 파라미터 발굴은 기존 repair 채널(`synth_params`)이 이미 수행 — 중복 구현 안 함.

### 4.3 `shape.apply(toolset) -> dict` 

반환: `{"renamed": int, "promoted": int, "skipped": [{"name","why"}]}` — connect가 출력.
skipped는 honest report 원칙대로 사유와 함께 보고.

## 5. 참조 해석 (하위 호환의 실체)

`by_name()`이 alias를 해석하므로 **코드 수정 없이** 통과되는 경로:
- dispatch/`confirm_and_run`/`agent.run_chat`의 `by_name().get(name)`
- evals: `tasks.validate`·grader의 `state` 체크·runner cleanup (모두 by_name 경유)
- lessons `_references_known_tools` — names 집합에 alias 포함됨

수정 필요 경로:
- `spec.py by_name()` 자체 (alias 등록)
- `verifier.agent_e2e`의 `names` — `by_name().keys()`라 자동 해결
- `toolrag.score`의 hay에 aliases 추가 (구 이름 검색어도 히트)

## 6. `eval --compare <old_toolspec>` (cli)

1. 현재 toolset과 old toolset 로드, 동일 evals.json 태스크 사용
2. 태스크 참조 도구명은 **양쪽 by_name(alias 포함)으로 각각 검증** — 한쪽에서 invalid면 그 쪽 집계에서 제외하고 보고
3. `task_eval`을 두 번 실행 (old 먼저, budget 2배 재설정), 리포트:

```
[compare] old: rate=0.75 avg_tools=3.2   new: rate=0.88 avg_tools=2.1
[compare] verdict: ✅ non-inferior rate AND fewer calls — keep the new toolset
```
verdict 규칙: `new.rate >= old.rate - 0.05`(non-inferior) AND `new.avg_tool_calls <= old.avg_tool_calls` → keep; rate 하락 시 ❌ revert 권고. history에는 **현재(new) 실행만** 기록(비교의 old 실행은 이력 오염 방지 위해 제외).

## 7. connect 통합

- scan 직후 `shape.apply(toolset)` 기본 실행, `--no-shape` 플래그로 opt-out
- OpenAPI 경로도 동일 적용 (operationId가 이미 좋은 이름이면 기계 변환 패턴과 불일치 → 보수적 폴백이 자동으로 보존)
- 출력: `[connect] shaping: renamed=5 promoted=1 skipped=0`

## 8. Error Handling

| 상황 | 동작 |
|------|------|
| 이름 충돌 (두 라우트가 같은 `<resource>_<action>`) | 뒤쪽 도구는 path 수식어 추가(`notes_get_by_note_id`), 그래도 충돌이면 기존 이름 유지 |
| alias가 다른 도구의 정식 이름과 충돌 | alias 등록 skip (정식 이름 우선) — by_name에서 조용히 우선순위 적용 |
| 이미 셰이핑된 toolspec 재로드 | meta.shaping 확인 → no-op |
| --compare에서 old 파일 없음/파손 | exit 2 + 사유 |

## 9. Test Plan

- 재명명 표 전 케이스 (notes-api 5개 도구 → 기대 이름 고정) + 중첩 리소스 + 충돌 폴백 + RPC POST
- 멱등성: apply 2회 → 2회째 no-op
- alias 해석: by_name·old-name evals 태스크 validate 통과·lessons stale 판정
- list 승격: limit 추가·기존 limit 보존·설명 suffix
- --compare: task_eval 스텁으로 verdict 3분기 (keep/revert/tie)
- 회귀: 기존 39개 무파손

## 10. Implementation Order

1. [ ] `spec.py` aliases + by_name 해석 + 직렬화
2. [ ] `shape.py` 재명명 + 승격 + 멱등 + 테스트
3. [ ] connect 통합(`--no-shape`) + toolrag hay
4. [ ] `eval --compare` + 테스트
5. [ ] notes-api 실동작 확인 + 문서
