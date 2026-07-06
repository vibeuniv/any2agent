# tool-composition (Phase 2) — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: 워크플로 합성 도구 — LLM 제안(인터랙티브 승인) + 결정적 다중 스텝 실행기($input/$steps 바인딩·정직한 부분 실패·플래그 MAX 상속)
> **PDCA Cycle**: 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../tool-consolidation/tool-consolidation.plan.md) (FR-04/05) · [design](tool-composition.design.md) · [analysis](tool-composition.analysis.md)
> **Commits**: `56f9808` (composite workflow tools) · `58b4988` (composite×render 회귀 + gap findings 96.8%)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | Phase 1 재명명 후에도 `notes_list → 하나 골라 → notes_get`은 에이전트가 2턴에 나눠 수행 — 중간 목록 전체가 컨텍스트로 유입되는 왕복 비용. Anthropic 가이드 "사람이 업무를 나누는 단위로 도구를 설계"(`schedule_event`=조회+예약) 미이행. |
| **Solution** | 합성 도구를 `backing.composite`(ToolSpec 스키마 무변경)로 노출: `compose.propose`가 LLM 우선(무키 시 list→detail 결정적 폴백·history chain 마이닝)으로 후보 생성 → **필수 인터랙티브 승인** → `core/composite.run`이 `$input`/`$steps` 바인딩으로 서버측 순차 실행 → 최종 결과만 반환(1 툴콜). |
| **Function/UX Effect** | 멀티스텝 작업이 합성 도구 1회 호출로 단축. 부분 실패 시 **롤백 안 함을 계약으로 못박고** 수행 스텝을 정직 보고. write/danger 플래그는 구성 스텝의 MAX를 상속해 확인 게이트가 합성 도구 자체에 발동. |
| **Value Delivered** | 라이브 2-스텝 합성(`notes_list→notes_get`) 실행 확인. Gap 분석 **96.8%**, tests **94/94**. 자동 채택용 `--yes` 부재가 곧 안전 경계 — "자동화가 안전을 대체하지 않는다"는 가이드 원칙을 구조로 구현. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| Option C — 실행기 `core/composite.py`(dispatch sibling) + 제안 `compose.py`(CLI), adapter는 단일 호출 유지 | ✅ | dispatch는 "합성이면 위임"만; adapter 계약 무파괴 |
| ToolSpec 스키마 변경 없음 — 합성 정보는 free-form `backing.composite`에 | ✅ | 직렬화/검증/toolrag 전부 additive |
| dispatch가 저장값 불신 — 스텝에서 `effective_flags` **재계산**해 게이트 | ✅ | 수동 편집 오류에도 안전(write 스텝 있으면 MAX=write) |
| 인터랙티브 승인 강제, 자동 채택용 `--yes` **없음** | ✅ | 승인이 안전 경계; `--dry-run`은 절대 미기록·미백업 |
| danger 스텝·중첩 합성 금지 | ✅ | validate + 실행기 양쪽 거부; `_llm_propose` 카탈로그에서 danger 제외(심층 방어) |
| 부분 실패 = 롤백 안 함 **명시**(`rolled_back:false` 항상), write 스텝 후 실패 시 side-effect 경고 | ✅ | 합성은 트랜잭션 아님을 계약으로 못박음(정직 보고) |
| 무LLM 결정적 폴백(list→detail pair) + `source: llm/chain/pair` 표기 | ✅ | 키 없이도 후보 제안, 출처 정직 |

## 3. Success Criteria — Final Status (design §SUCCESS / plan FR-04·05)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | notes-api 2-스텝 합성(`notes_list→notes_get`) 라이브 실행 | ✅ | 실행기 notes-api 라이브 검증(무LLM 경로) |
| SC-2 | 부분 실패 시 수행 스텝 정직 보고 | ✅ | `completed`/`failed_step`/`rolled_back:false` + write side-effect note |
| SC-3 | write 합성이 확인 게이트 우회 안 함 (MAX 상속) | ✅ | `confirmed=False`→`confirm_required`, `True`→실행 (테스트) |
| SC-4 | 기존 52 테스트 무파손 + `eval --compare` 채택 권고 | ✅ | 94/94; 채택 시 `precompose.json` 백업 + compare 권고 출력 |

**성공률: 4/4 (100%)**. FR-07(마이그레이션 커맨드)은 계획대로 **descoped**.

## 4. Gap-Analysis Summary

- **96.8%** `(29 full + 2 partial)/31` (FR-07 의도적 descope — 채점 제외). FR-04·05 모두 충족.
- **핵심 발견 — 크로스-기능 회귀 (High, 동일 세션 수정)**: composite 자체는 설계와 일치했으나 **이후 커밋(response-shaping)과의 상호작용**에서 회귀 발견. 성공 스텝 레코드가 `data` 전체를 담고 `respond.render`가 `steps[*].data`를 셰이핑/캡 없이 통과 →
  1. 중간 목록 전체가 LLM 컨텍스트로 유입 (**composite의 존재 이유 무효화**),
  2. "항상 ≤ cap" 보장 위반 가능(스텝 data가 halving 루프 밖),
  3. composite 내부 에러가 transport 힌트로 오진단 / 스텝 http_404 형제 제안 소실.
- **조치 (94/94)**: 성공 스텝 레코드에서 data 제거(§4.2 설계 형태 복원, 바인딩은 raw results를 읽으므로 무영향), render 경로가 스텝 data를 `_fit`으로 바운드, `_explain_composite` 전용 힌트(binding_error/unknown_tool/nested + `http_NNN` 형제 제안 부활), danger 카탈로그 노출 제외. **신규 회귀 테스트 4종**.

## 5. Lessons Learned

- **크로스-기능 회귀는 단일 feature 검증을 통과한다**: composite는 자기 설계엔 100% 부합했지만, 나중에 머지된 response-shaping이 스텝 data를 캡 없이 흘려 "composite의 존재 이유"를 무효화했다. 갭 분석에 "response-shaping과의 상호작용 포함" 지시를 명시적으로 넣은 것이 이 회귀를 잡은 결정타 — **feature 경계가 아니라 데이터 흐름을 따라 검증해야 한다**.
- **정직한 부분 실패 > 가짜 트랜잭션**: 합성을 트랜잭션으로 위장하는 대신 `rolled_back:false`를 계약으로 못박아, 사용자가 수동 정리할 대상을 항상 알 수 있게 함.
- **문서 간 테스트 수 표기 드리프트**: plan 24 / design 52 / 실제 94로 문서마다 다름 — 기능엔 무해하나 문서 신뢰도 저하. 리포트에 실측(94)을 정본으로 기록.

## 6. Remaining / Deferred

- LLM 제안 경로는 mock 한계로 스텁 테스트 — 실키 환경 검증 몫.
- composite 스텝별 텔레메트리(현재는 도구 단위 1건 기록) — 후속(Task #17).
- 마이그레이션 커맨드(FR-07) — descope 유지.
