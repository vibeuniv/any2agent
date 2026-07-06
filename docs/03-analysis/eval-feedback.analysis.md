# eval-feedback — Gap Analysis

> **Feature**: eval-feedback (이력·선별 출력·실패 분류·lessons 주입)
> **Design Doc**: [eval-feedback.design.md](../02-design/features/eval-feedback.design.md)
> **Analyzed**: 2026-07-05 (gap-detector agent)

## Match Rate: 95.6% ✅

`(31 full × 1.0) + (3 partial × 0.5) + (0 miss) = 32.5 / 34 designed items = 0.956`

FR 6/6 충족, 성공 기준 5/5 충족(1건은 코드 존재·테스트 부재). 누락 기능 0.

## Partial-match 3건

| Item | 상태 | 내용 |
|---|:--:|---|
| `classify` 시그니처 | ⚠️ | 설계 §1 다이어그램(`classify(result)`)과 §3.2(`classify(result, task)`)가 상호 모순 — 코드는 다이어그램과 일치, `task` 인자는 원래 불필요 |
| lessons 파손 파일 경고 | ⚠️ | 설계 §5 "경고 출력" vs 코드는 조용히 빈 목록 시작 (다음 저장 시 재생성은 일치) |
| stale 도구 lesson 제거 시점 | ⚠️ | 설계 "로드/저장 시" vs 코드는 저장(merge_save) 시에만 — 실사용상 파일은 깨끗하게 유지됨 |

## Deviations (경미)

- **D1 (Low)**: `lessons.render()`가 런타임에서 미사용 — 주입 헤더 문구가 `lessons.py`와 `agent.py` 두 곳에 중복 (드리프트 위험)
- **D2 (Low)**: stale 판정 휴리스틱이 snake_case 토큰 매칭 — 현 도구 명명(`get__notes`)에서는 유효
- **D3 (improvement)**: `build()`가 runner/infra 실패를 lesson에서 제외 — 인프라 분리 원칙과 일관
- **D5 (trivial)**: `when` 절단 160 vs 120 혼용

## 테스트 커버리지 공백

- MAX-20 상한 (구현됨, 전용 테스트 없음)
- `--fix` toolspec 저장 (구현됨, 자동 테스트 없음)

## Post-analysis fixes (applied 2026-07-05, same session)

| 항목 | 조치 |
|---|---|
| D1 헤더 중복 | `agent._inject_lessons`가 `lessons.render()` 재사용 (lazy import, 순환 없음) |
| 파손 경고 | `lessons.load()`가 파손 파일 감지 시 경고 1줄 출력 (설계 §5 그대로 충족) |
| classify 시그니처 | 설계 §3.2를 `classify(result)`로 정정 (다이어그램 기준) |
| stale 제거 시점 | 설계 §5를 "저장 시 제거"로 명확화 |
| 테스트 공백 | MAX-20 상한 테스트 + `--fix` toolspec 저장 테스트 추가 |

**Post-fix Match Rate: ~99%** (잔여: D2 휴리스틱 문서화 수준, D5 trivial)

## Recommended next actions

- `/pdca report eval-feedback` (또는 self-verification과 묶어 보고)
- 후속: tool-consolidation 진행 시 D2 휴리스틱 재검토 (도구 재명명과 직접 연관)
