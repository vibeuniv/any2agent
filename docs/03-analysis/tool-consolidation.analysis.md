# tool-consolidation Phase 1 — Gap Analysis

> **Feature**: tool-consolidation Phase 1 (결정적 셰이핑)
> **Design Doc**: [tool-consolidation.design.md](../02-design/features/tool-consolidation.design.md)
> **Analyzed**: 2026-07-06 (gap-detector agent) · 병행 라이브 검증 4/4 통과

## Match Rate: 98.5% ✅

`(33 full × 1.0) + (1 partial × 0.5) + (0 miss) = 33.5 / 34 designed items = 0.985`

Phase-1 FR 5/5 충족(FR-01/02/03/06/08). 미묘한 규칙까지 전부 일치 확인:
RPC-POST 충돌 시 기존 이름 유지, `/health` 싱글턴, budget 2×, non-inferior −0.05,
**compare의 old 실행이 history/lessons를 오염시키지 않음**, OpenAPI fast-path도 셰이핑됨.

## 발견 및 조치 (동일 세션 적용)

| ID | 발견 | 심각도 | 조치 |
|---|---|:---:|---|
| H4 | `--compare` 파손 파일이 uncaught traceback (설계 §8은 exit 2 약속) | Low | `ToolSet.load`를 try/except로 감싸 exit 2 + 사유 |
| §9 | `--compare` verdict 3분기 테스트 부재 (유일한 미테스트 High FR) | Med(coverage) | `test_compare.py` 신설 — keep/revert/tie + missing/corrupt exit 2 |
| §9 | lessons stale 판정의 alias 해석 미검증 | Low | `test_stale_detection_resolves_aliases` 추가 |
| D1 | `_has_collection_shape` 복수형 휴리스틱이 설계에 없음 (설계 §4.1이 자기모순) | Info | 설계 action 표에 복수형 규칙 백포트 |
| D6 | 설계 §2 "순수 패스" 문구 vs §4.3 in-place 계약 | Info | §2 문구를 §4.3 기준으로 정정 |
| D2/D3/D5 | noop 키·400자 캡·출력 prefix | Cosmetic | 무시 (기능 동일) |

## 라이브 검증 (병행 수행)

- fresh `connect`: `shaping: renamed=5 promoted=1 skipped=0` → verify 통과
- `--no-shape`: 기계식 이름 유지
- LLM 페이로드: 정식 이름만 노출 (alias 비노출)
- 채팅 E2E: mock이 구 이름 `get__notes` 호출 → alias 해석 → 라이브 API 정상 실행

**Post-fix: 52/52 tests. Verdict: Phase 2 진행 가능.**
