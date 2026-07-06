# runtime-telemetry — Gap Analysis

> **Design Doc**: [runtime-telemetry.design.md](runtime-telemetry.design.md)
> **Analyzed**: 2026-07-06 (gap-detector)

## Match Rate: 88% → post-fix ~100% ✅

`23.0 / 26 = 0.885` — 게이트(90%) **미달**로 판정, Act에서 즉시 수정.
코어 모듈(§2)·기록 지점(§3)은 26개 중 14개 전항목 완전일치 — 감점은 전부 노출 경계(§4)와
테스트 커버리지(§6).

## 핵심 발견 — D1 (Important)

서버는 eval 미실시 프로젝트도 `runtime`을 반환하도록 의도적으로 게이트를 넓혔는데
(**calls_total 포함**), **두 클라이언트가 `!d.evaluated`에서 조기 반환**해 Live-usage
카드와 배지 ⚠가 렌더되지 않았다. 즉 "eval을 한 번도 안 돌린 채 배포된 에이전트"가
라이브 실패를 쌓아도(의심 판정은 라이브 5회만으로 발동 가능) 경고가 숨겨짐 —
"상시 신호"라는 Core Value와 FR-06/07 정면 모순.

## 조치 (동일 세션, 104/104 tests)

| 발견 | 조치 |
|---|---|
| D1: UI가 evaluated에 게이트됨 | `liveUsageCard(d)` 함수로 추출해 not-evaluated 분기에서도 렌더; 배지는 `— not evaluated ⚠` 형태로 suspects 병기 |
| D2: run_chat 기록 계약 미테스트 | run_chat 스텁 테스트 — 실행 도구 1건=1줄, confirm_required=0줄 (FR-05 고정) |
| #21: 파손 줄 skip 미테스트 | corrupt-line 테스트 추가 |
| #23: avg_ms 미단언 | summary avg_ms 값 단언 추가 |
| D3: auto_confirm 타이밍이 게이트 dispatch 포함 | 무시 (µs 단위, 분석도 no-action 판정) |

## FR 커버리지: 7/7 (수정 후)

FR-01~04 완전(분석 시점부터), FR-05 코드+테스트 확보, FR-06/07 D1 수정으로 완전.

## 부기

- 도움말: `docs/HOW-EVAL-WORKS.md` + 콘솔 "How does this work?" 패널(빈 상태 포함
  모든 화면에서 접근 가능) — eval 3신호 원리·정직한 분모·lessons·telemetry를 평문 설명
- 라이브 검증(설계 성공 기준의 수동 항목)은 구현 세션에서 기수행: 실채팅 1건 기록(8ms),
  주입 실패 6건 → 의심 배너·배지 확인
