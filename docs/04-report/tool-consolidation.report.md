# tool-consolidation (Phase 1) — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: 결정적 도구 셰이핑 — `resource_action` 재명명 + alias 호환 + list→search 승격 + `eval --compare`
> **PDCA Cycle**: 2026-07-05 ~ 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../01-plan/features/tool-consolidation.plan.md) · [design](../02-design/features/tool-consolidation.design.md) · [analysis](../03-analysis/tool-consolidation.analysis.md)
> **Commits**: `a47c992` (deterministic tool shaping) · `8c9d50a` (gap findings 98.5%→~100%)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | 스캐너가 1 라우트 = 1 도구를 기계 변환(`get__notes` 이중 언더스코어, list 도구가 전체 목록 반환) — Anthropic 가이드 "API 엔드포인트를 그대로 래핑하지 말라, 에이전트는 컨텍스트가 비싸다"를 정면 위반. |
| **Solution** | 스캔과 verify 사이에 **결정적 셰이핑 패스**(`shape.py`) 추가: (1) path 구조에서 리소스·동작 추출해 `<resource>_<action>` 재명명, (2) 기존 이름은 `ToolSpec.aliases`로 보존, (3) list 도구에 `limit` 승격 + "전체 대신 필터" 유도, (4) `eval --compare`로 전/후 A/B 측정. |
| **Function/UX Effect** | 에이전트가 `notes_list`·`notes_get`처럼 의도가 드러나는 도구를 사용 — 도구 오선택·컨텍스트 낭비 감소. 기존 toolspec/evals/lessons는 **alias 해석으로 무파손**(코드 수정 최소). |
| **Value Delivered** | "자동 생성" 가치를 유지하며 생성물 품질을 사람 설계 수준으로. 라이브 검증 `renamed=5 promoted=1 skipped=0` → verify 통과. Gap 분석 **98.5% → ~100%**, tests **52/52**. 개선을 "감"이 아니라 eval 수치로 증명 가능. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| Option C — 단일 `shape.py`(in-place 변형 + 통계 dict) + `spec.py` alias, 마이그레이션 CLI 과설계 회피 | ✅ | `shape.apply(toolset) -> {renamed, promoted, skipped}` 순수 패스 |
| **보수적 폴백**: 리소스 추출 불가·충돌·사람이 고친 흔적(기계 패턴 불일치) → 기존 이름 유지 | ✅ | OpenAPI operationId처럼 이미 좋은 이름은 자동 보존, danger 없이 안전 |
| alias 해석은 `by_name()`에서 (코드 경로 수정 최소) | ✅ | dispatch/evals/lessons/grader가 전부 `by_name` 경유 → 무수정 통과 |
| list→search: `limit` 파라미터 승격 + 설명 유도, LLM 필터 발굴은 기존 `synth_params` 재사용(중복 구현 안 함) | ✅ | 백엔드가 limit 무시해도 무해(잉여 쿼리 파라미터) |
| `meta.shaping` 버전으로 멱등 (재실행 no-op) | ✅ | 이미 셰이핑된 toolspec 재로드 시 안전 |
| `eval --compare` verdict: non-inferior(−0.05) rate AND fewer calls → keep, old 실행은 이력 비오염 | ✅ | 라이브에서 old 실행이 history/lessons 오염 안 함 확인 |

## 3. Success Criteria — Final Status (plan §4)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | notes-api에서 `notes_list/get/create/delete` 형태 생성 | ✅ | 라이브 `renamed=5`, 재명명 표 전 케이스 테스트 |
| SC-2 | 구 이름(`get__notes`)으로 저장된 evals.json이 alias 해석으로 동작 | ✅ | chat E2E: mock이 구 이름 호출 → alias 해석 → 라이브 실행 |
| SC-3 | `eval --compare`가 전/후 완수율·호출수 비교 리포트 | ✅ | `test_compare.py` keep/revert/tie 3분기 + missing/corrupt exit 2 |
| SC-4 | 전체 pytest 통과 (기존 + 신규) | ✅ | 52/52 |
| SC-5 (§4.1) | composite 도구 1개 이상 동작 | ↪ | **Phase 2로 이관** — 본 사이클 §2.1이 composite를 명시적 out of scope(A/B 검증 후 착수)로 확정 |

**성공률: Phase 1 범위 4/4 (100%)** — composite DoD 항목은 계획대로 Phase 2(tool-composition)로 분리·이후 완료.

## 4. Gap-Analysis Summary

- **초기 98.5%** `(33 full + 1 partial)/34` — Phase-1 FR 5/5, 미묘한 규칙(RPC-POST 충돌 시 기존 이름 유지, `/health` 싱글턴, budget 2×, non-inferior −0.05, compare old 실행 비오염)까지 일치.
- **동일 세션 수정 → ~100%**:
  - **H4**: `--compare` 파손 파일이 uncaught traceback(설계 §8은 exit 2 약속) → `ToolSet.load` try/except로 exit 2 + 사유.
  - **§9 커버리지**: `--compare` verdict 3분기 미테스트(유일한 미테스트 High FR) → `test_compare.py` 신설. alias stale 판정 테스트 추가.
  - **D1**: `_has_collection_shape` 복수형 휴리스틱이 설계에 없음(설계 §4.1 자기모순) → 설계 action 표에 백포트. **D6**: §2 "순수 패스" 문구를 §4.3 in-place 계약 기준으로 정정.

## 5. Lessons Learned

- **가장 위험한 도구가 가장 덜 테스트됨**: `--compare`(채택 결정을 좌우하는 High FR)가 유일한 미테스트 High 항목이었다 — "중요도 ≠ 테스트 커버리지"를 갭 분석이 정확히 짚었다.
- **alias 레이어가 하위 호환의 실체**: 이름을 바꾸면서도 `by_name()` 한 곳만 고쳐 dispatch/evals/lessons 전부를 무수정으로 통과시킨 것이 핵심 — 파괴적 리네이밍을 비파괴적으로 만든 설계.
- **eval 하네스 전제의 값어치**: 이 feature는 self-verification 완료를 전제로만 착수 가능했다 — 재명명 효과를 완수율·호출수로 A/B 검증할 수단이 있었기에 "감으로 바꾸지 않았다".

## 6. Remaining / Deferred

- **워크플로 합성(composite) 도구 → Phase 2 (tool-composition)** — 이후 착수·완료.
- 마이그레이션 커맨드(FR-07) — alias가 해결하므로 Phase 1엔 불필요, Phase 2에서도 descope.
- toolrag 임베딩 검색 고도화 — 후속(Task #16).
