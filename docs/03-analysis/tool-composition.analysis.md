# tool-composition — Gap Analysis

> **Design Doc**: [tool-composition.design.md](../02-design/features/tool-composition.design.md)
> **Analyzed**: 2026-07-06 (gap-detector) — cross-feature interaction with response-shaping 포함 지시

## Match Rate: 96.8% ✅

`(29 full + 2 partial × 0.5) / 31 = 0.968` (FR-07은 의도적 descope — 채점 제외).
FR-04(제안+필수 승인+danger 금지)·FR-05(실행기+MAX 플래그+정직한 부분 실패) 모두 충족.

## 핵심 발견 — 크로스-기능 회귀 (High)

composite 자체는 설계와 일치했으나, **이후 커밋(response-shaping)과의 상호작용**에서
회귀 발견: 스텝 레코드가 성공 스텝의 `data` 전체를 담고, `respond.render`가
`steps[*].data`를 셰이핑/캡 없이 통과 —

1. 중간 목록 전체가 LLM 컨텍스트로 유입 (composite의 존재 이유 무효화)
2. "항상 ≤ cap" 보장 위반 가능 (스텝 데이터는 halving 루프 밖)
3. composite 내부 에러가 transport 힌트로 오진단 / 스텝 http_404의 형제 제안 소실

## 조치 (동일 세션, 94/94 tests)

| 발견 | 조치 |
|---|---|
| 스텝 `data` 유입 (High) | 성공 스텝 레코드에서 data 제거(§4.2 설계 형태로 복원) — 바인딩은 records가 아닌 raw results를 읽으므로 무영향. 최종 data는 `_report(final_data=)`로 전달. **실패 스텝만** 진단용 data 유지 |
| cap 위반 (High) | render 에러 경로가 스텝 data를 shape로 바운드, `_fit`이 스텝 data→data 순으로 점진 제거, 성공 경로 최종 분기도 `_fit` 경유 |
| 오진단 힌트 (Med) | `_explain_composite`: binding_error/unknown_tool/nested/config 전용 문구 + `http_NNN`은 실패 도구 spec으로 상태 표 재사용(형제 제안 부활) |
| danger 카탈로그 노출 (Low-Med) | `_llm_propose` 카탈로그에서 danger 도구 제외 (validate 차단에 더한 심층 방어) |
| nested validate 직접 테스트 부재 (Low) | 단위 테스트 추가 |

신규 회귀 테스트 4종: 성공 스텝 무데이터, 실패 스텝 데이터 유지+render 바운드+힌트,
composite 에러 힌트 비-transport, nested validate 직접 거부.

## 잔여 (정직 보고)
- 문서 간 회귀 테스트 수 표기 드리프트(plan 24/design 52/실제 94) — 문서만의 문제
- LLM 제안 경로는 스텁 테스트 (mock LLM 한계, 실키 환경 몫)
