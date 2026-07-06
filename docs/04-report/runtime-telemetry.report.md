# runtime-telemetry — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: 런타임 도구 호출 기록(`tool-calls.jsonl`) + 드리프트 감지(의심 도구·자기해제) + 콘솔 Live-usage 카드 + 배지 `⚠`
> **PDCA Cycle**: 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../01-plan/features/runtime-telemetry.plan.md) · [design](../02-design/features/runtime-telemetry.design.md) · [analysis](../03-analysis/runtime-telemetry.analysis.md)
> **Commits**: `aea3b1b` (runtime telemetry + drift detection) · `b4438f0` (always-on drift signal + eval principles help, gap 88%→~100%)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | 배포 후 도구가 실제 대화에서 얼마나 호출·실패하는지 아무 기록이 없음. API가 바뀌어 도구가 계속 깨져도(드리프트) eval을 수동 재실행 전까지 아무도 모름. |
| **Solution** | dispatch 결과를 `telemetry.record`로 jsonl에 append(**이름·결과 코드·시간만** — args/응답/신원 비기록), 도구별 최근 에러율로 **의심 도구**(최근 10회 중 과반 실패 AND 표본 ≥5) 판정, `/evals`·UI Live-usage 카드로 노출하고 재검증(`eval`) 제안. |
| **Function/UX Effect** | 콘솔에서 "실전에서 어떤 도구가 죽고 있는가"가 보이고, 드리프트 감지 시 배지·콘솔이 재검증을 촉구. 기록 실패는 **어떤 경우에도 대화를 안 깸**(전 예외 흡수), 파일 자동 로테이션(5000→2500), 판정은 최근 창만 보므로 복구 시 자연 해제. |
| **Value Delivered** | 검증이 1회성 게이트가 아니라 **상시 신호**가 됨 — 가이드 대응 로드맵의 마지막 조각. Gap **88% → ~100%**, tests **104/104**. 라이브: 실채팅 1건 기록(8ms), 주입 실패 6건 → 의심 배너·배지 확인. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| 이름·결과·시간만 기록, args/응답/신원 절대 비기록 (memory 규율) | ✅ | 시크릿·개인정보 유출 방지; 닫힌 스키마 |
| 기록 실패 no-raise (전 예외 흡수) — 대화 우선 | ✅ | 쓰기 불가 디렉터리 테스트 포함 |
| 로테이션 5000→2500, summary window=10 | ✅ | jsonl 무한 성장 방지, tail 윈도 집계 |
| suspect: 최근 window 에러율 ≥0.5 AND 표본 ≥5, 플래그 미저장(자연 해제) | ✅ | 복구되면 다음 창에서 자동 해제 |
| authz(401/403)는 에러로 미집계, `confirm_required`는 미기록(실행 아님) | ✅ | RBAC 거부·확인 대기를 실패로 오집계 안 함 |
| composite는 dispatch가 1결과 반환 → 합성 이름으로 1건 기록 | ✅ | 스텝별 기록은 후속(도구 단위 신호로 충분) |

## 3. Success Criteria — Final Status (plan)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | 채팅 1회 → telemetry jsonl 1줄 (라이브) | ✅ | 라이브 실채팅 1건 기록(8ms) |
| SC-2 | 강제 실패 반복 → 콘솔 의심 도구 + 재검증 제안 (라이브) | ✅ | 주입 실패 6건 → 콘솔 suspect 배너 + 배지 `⚠` |
| SC-3 | 기존 94 테스트 무파손 + 신규 단위 테스트 | ✅ | 104/104 (`test_telemetry.py`) |
| SC-4 | Gap 분석 ≥ 90% | ✅ | 88% → ~100% (게이트 미달 → Act 즉시 수정) |

**성공률: 4/4 (100%)** — 단, SC-4는 **초기 88%로 게이트(90%) 미달** → 동일 세션 D1 수정 후 통과(§4).

## 4. Gap-Analysis Summary

- **초기 88%** `23.0/26` — **게이트(90%) 미달로 판정, Act에서 즉시 수정**. 코어 모듈(§2)·기록 지점(§3) 14개 전항목 일치, 감점은 전부 노출 경계(§4)와 테스트 커버리지(§6).
- **핵심 발견 — D1 (Important)**: 서버는 eval 미실시 프로젝트도 `runtime`(calls_total 포함)을 반환하도록 게이트를 넓혔는데, **두 클라이언트가 `!d.evaluated`에서 조기 반환**해 Live-usage 카드·배지 `⚠`가 렌더 안 됨. 즉 "eval을 한 번도 안 돌린 채 배포된 에이전트"가 라이브 실패를 쌓아도(의심 판정은 라이브 5회만으로 발동) 경고가 숨겨짐 — **"상시 신호"라는 Core Value와 FR-06/07 정면 모순**.
- **조치 (104/104)**: `liveUsageCard(d)` 함수로 추출해 not-evaluated 분기에서도 렌더, 배지는 `— not evaluated ⚠`로 suspects 병기. run_chat 기록 계약 테스트(실행 도구 1건=1줄, `confirm_required`=0줄), corrupt-line skip 테스트, summary avg_ms 단언 추가. FR 7/7 달성.

## 5. Lessons Learned

- **"항상 보이게" 설계가 클라이언트에서 새면 무의미**: 서버는 상시 신호를 의도해 게이트를 넓혔는데 두 UI 클라이언트가 `!evaluated`에서 조기 반환해 **가장 중요한 사용자(eval 안 돌린 채 배포한 사람)에게 경고가 안 보였다**. Core Value 위반은 서버 계약이 아니라 렌더 분기에 숨어 있었다 — 갭 분석이 "의도 vs 실제 노출"을 끝까지 추적해 잡음.
- **게이트 미달을 정직하게 기록**: 7개 feature 중 유일하게 초기 점수가 90% 게이트 미달(88%)이었고, 이를 숨기지 않고 Act에서 즉시 수정 후 리포트에 명시 — PDCA의 정직 보고 규율이 작동한 사례.
- **eval(빌드타임)과 telemetry(런타임)의 짝**: 같은 콘솔에서 "검증했을 때 통과했나"(eval)와 "실전에서 살아 있나"(telemetry) 두 신호를 나란히 보여, 검증을 시점이 아니라 연속으로 만듦.

## 6. Remaining / Deferred

- composite 스텝별 텔레메트리(현재 도구 단위 1건) — 후속(Task #17).
- 자동 eval 트리거·드리프트 알림(웹훅) — 의도적 out of scope, 후속(Task #17).
- 도움말: `docs/HOW-EVAL-WORKS.md` + 콘솔 "How does this work?" 패널로 3신호·정직한 분모·lessons·telemetry를 평문 설명(완료).
