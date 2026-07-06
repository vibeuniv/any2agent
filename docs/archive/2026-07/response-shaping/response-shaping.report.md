# response-shaping — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: LLM-facing 도구 응답 렌더 레이어 — 구조 인지 절단(`_meta.truncated`) + `response_format` concise/detailed + actionable 에러 힌트
> **PDCA Cycle**: 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](response-shaping.plan.md) · [design](response-shaping.design.md) · [analysis](response-shaping.analysis.md)
> **Commit**: `d5bf756` (response shaping — token-efficient tool results + actionable error hints)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | 도구 응답이 raw JSON 그대로 LLM에 유입되고 유일한 방어가 직렬화 후 6000자 절단 — JSON이 **구조 중간에서 잘려 깨진 데이터**가 들어감. 에러는 `http_404`·raw 예외 문자열뿐이라 에이전트가 "무엇을 고칠지" 모름. |
| **Solution** | `respond.py` 신설: 배열은 **아이템 단위** 절단하고 "N/M개 표시 — limit/필터를 쓰라" 유도 문구 동봉, `response_format`(concise/detailed)를 list 도구에 승격, 에러는 상태코드별 actionable 힌트로 변환(404면 리소스 명명 기반 형제 도구 `notes_list` 제안). |
| **Function/UX Effect** | 에이전트가 항상 온전한(파싱 가능) 구조를 받고, 실패 시 다음 행동이 힌트로 주어져 자가 복구율 상승. 대용량 목록이 컨텍스트를 태우지 않음. **핵심 불변식**: 셰이핑은 LLM 메시지 전용 — adapter 반환·UI 이벤트·grader state 재조회는 원본 불변. |
| **Value Delivered** | 가이드의 토큰 효율·에러 응답 원칙 구현 — eval로 개선을 측정할 수 있는 마지막 큰 갭 해소. Gap 분석 **94.4% → ~100%**, tests **90/90**. 라이브: 40개 배열 → 10개 + `_meta`, 실제 422 → 힌트+서버 detail 동봉. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| respond는 `_tool_msg`(LLM 메시지) **전용** — 원본은 불변 | ✅ | grader state check·verifier liveness·SSE·EvalTrace는 원본 소비 → 채점/UI 무오염 |
| `render`는 cap 초과 시 max_items 절반씩 축소(10→5→2→1), 최후 `_meta.omitted` — **항상 유효 JSON** | ✅ | 어떤 경우에도 `json.loads` 가능 (mid-slice 금지) |
| 404 형제 제안은 **결정적 규칙만** (`rsplit("_",1)` 접두사 + `list/search` 접미사) | ✅ | 비셰이핑(기계식 이름) toolset에선 자연 미발동 — 잘못된 유도 없음 |
| `response_format`: 스키마 승격 + dispatch **전에 pop** (백엔드 유출 금지) | ✅ | run_chat·confirm 재진입 양쪽 pop; eval runner는 자동 적용(pop된 args 무해 기록) |
| concise는 null/빈 필드 제거·좁은 절단폭, detailed는 필드 전부 보존(후속 호출용 ID) | ✅ | 가이드의 "detailed는 ID 포함" 원칙 충족 |
| `SHAPING_VERSION` 1→2 — 재실행 시 기존 toolspec도 승격, 재명명은 alias로 멱등 | ✅ | v1 아티팩트가 v2 승격 시 `renamed=0`·잡음 0(carry-forward audit) |

## 3. Success Criteria — Final Status (plan §3)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | 대형 배열이 절단 고지와 함께 유효 JSON으로 LLM 전달 | ✅ | `test_respond.py`; 라이브 40개→10개 + `_meta.truncated` |
| SC-2 | 404 LLM 메시지에 형제 도구 제안 포함 | ✅ | `explain` 404 분기 단위 테스트 (라이브 404는 스텁 한계) |
| SC-3 | 기존 74 테스트 무파손 + notes-api 라이브 확인 | ✅ | 90/90; 실제 FastAPI 422 → 힌트+서버 detail 동봉 |
| SC-4 | Gap 분석 ≥ 90% | ✅ | 94.4% → ~100% |

**성공률: 4/4 (100%)**

## 4. Gap-Analysis Summary

- **초기 94.4%** `(40 full + 5 partial)/45` — FR 6/6 구현(2건 partial), 누락 0.
- **동일 세션 수정 → ~100% (90/90)**:
  - **#1 (Med)**: `_meta.truncated {shown,total}` 구조가 hint 문자열에만 존재 → shape가 `truncations` 반환, render가 `_meta.truncated` 부착 + 테스트(프로그램적 접근 보장).
  - **#2 (Med)**: `confirm_and_run`이 `response_format`을 pop 안 함(직접 호출 시 백엔드 유출 가능) → 방어 pop + Spy adapter 테스트.
  - **#3 (Low)**: detailed 모드도 긴 문자열 마커 절단(설계 문구 불일치) → 설계를 코드 기준 정정(필드 보존이 본질, 마커 절단은 양 모드 공통).
  - **#4 (Low)**: `unknown_tool`이 transport 힌트 오유도 → 전용 힌트("pick from the tool list / search_tools").
  - 역방향(긍정): ours-set 잡음 억제·renamed carry-forward·`_fit`·에러 본문 셰이핑 → 설계 §2.1/§2.3/§4에 문서화.

## 5. Lessons Learned

- **"hint 문자열엔 있는데 구조엔 없다"**: 사람이 읽는 절단 고지(문자열)는 있었지만 기계가 읽는 `_meta.truncated` 구조가 빠져 있었다 — LLM/프로그램 소비를 위한 구조적 계약과 사람용 문구를 **둘 다** 제공해야 한다는 교훈.
- **pop 방어는 모든 진입점에**: 정상 경로(run_chat)는 pop으로 방어됐지만 `confirm_and_run` 직접 호출 경로가 새고 있었다 — 민감 파라미터 제거는 단일 경로가 아니라 모든 dispatch 진입점에서 보장해야 함.
- **원본 불변식이 회귀를 국소화**: "respond는 `_tool_msg` 전용"을 처음부터 못 박았기에, 이 feature가 채점·UI를 깨뜨리지 않았다. (단, 이 불변식을 **모르는** composite가 스텝 data를 흘려 tool-composition 회귀를 유발 — 크로스-feature 검증의 필요성.)

## 6. Remaining / Deferred

- 데모 API가 스텁이라 404 형제 제안은 단위 테스트로만 검증(라이브 404 불가) — 실 API 환경 몫.
- 페이지네이션 커서·필드 프로젝션 — 계획대로 후속(Task #16).
