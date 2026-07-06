# runtime-telemetry Planning Document

> **Summary**: 런타임 도구 호출 기록 + 도구별 에러율 + 드리프트 감지 — eval(빌드 타임)의 런타임 짝
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 배포 후 도구가 실제 대화에서 얼마나 호출되고 얼마나 실패하는지 아무 기록이 없다. API가 바뀌어 도구가 계속 깨져도(드리프트) eval을 수동으로 다시 돌리기 전까지 아무도 모른다. |
| **Solution** | dispatch 결과를 상태 디렉터리에 jsonl로 기록(`telemetry.py`), 도구별 최근 에러율을 집계해 **의심 도구**(최근 N회 중 과반 실패)를 판정, 콘솔(`/evals` + UI)에 런타임 섹션으로 노출하고 재검증(`eval`)을 제안한다. |
| **Function/UX Effect** | 콘솔에서 "실전에서 어떤 도구가 죽고 있는가"가 보이고, 드리프트가 감지되면 배지·콘솔이 재검증을 촉구한다. |
| **Core Value** | 검증이 1회성 게이트가 아니라 상시 신호가 된다 — 가이드 대응 로드맵의 마지막 조각. |

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 런타임 도구 실패가 무기록 — 드리프트를 감지할 수단이 없음 |
| **WHO** | 운영 중인 에이전트의 소유자 (콘솔 열람자) |
| **RISK** | 기록이 요청 경로를 느리게 하거나(동기 I/O), 민감 데이터(args/응답)를 남김 → 이름·결과 코드·시간만 기록, 실패해도 조용히 무시 |
| **SUCCESS** | 채팅 도구 호출이 jsonl에 남고, 의심 도구가 /evals·UI에 표시, 파일 자동 로테이션, 전체 테스트 무파손 |
| **SCOPE** | 기록·집계·드리프트 판정·콘솔 노출. 자동 eval 트리거·알림(웹훅)은 후속 |

## Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `telemetry.record(state_dir, tool, ok, status, ms)` — jsonl append, 실패 시 무해(no-raise), **args/응답/신원 미기록** | High |
| FR-02 | 로테이션: 파일 5000줄 초과 시 최근 2500줄만 유지 | High |
| FR-03 | `summary(state_dir)` — 도구별 {calls, errors, error_rate, avg_ms, last_ts} (최근 2000줄 윈도) | High |
| FR-04 | 드리프트 판정: 최근 window(기본 10회) 중 에러율 ≥ 0.5 AND 호출 ≥ 5 → suspect + "run eval" 제안 | High |
| FR-05 | 기록 지점: `agent.run_chat`의 dispatch 결과 + `confirm_and_run` (confirm_required는 기록 제외 — 실행이 아님) | High |
| FR-06 | `/evals`에 `runtime` 섹션 {calls_total, tools:[…], suspects:[…]}, UI에 "Live usage" 카드(의심 도구 강조) | High |
| FR-07 | 채팅 배지: suspects 존재 시 `⚠` 병기 | Medium |

## Success Criteria
- [ ] 채팅 1회 → telemetry jsonl 1줄 (라이브 확인)
- [ ] 강제 실패 반복 → 콘솔에 의심 도구 + 재검증 제안 표시 (라이브 확인)
- [ ] 기존 94 테스트 무파손 + 신규 단위 테스트
- [ ] Gap 분석 ≥ 90%

## Risks
| Risk | Mitigation |
|------|------------|
| 기록 I/O가 대화 지연 유발 | append 1줄 동기 쓰기(µs 단위) + 예외 전부 흡수 |
| authz(401/403)를 실패로 오집계 | RBAC 거부는 error로 세지 않음 (liveness와 동일 관례) |
| 의심 판정 후 복구돼도 계속 경고 | 판정은 항상 "최근 window"만 — 복구되면 자연 해제 |
