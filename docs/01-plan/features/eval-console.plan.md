# eval-console Planning Document

> **Summary**: eval 결과를 웹에서 확인 — 읽기 전용 `/evals` API + 채팅 UI 신뢰 배지 + `/evals/ui` 간이 대시보드
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | eval 수치·이력·실패 내역이 터미널과 파일에만 있어, 브라우저 사용자(채팅 UI 이용자·비개발 이해관계자)는 "이 에이전트가 검증된 상태인가"를 알 방법이 없다. |
| **Solution** | 이미 파일로 쌓이는 데이터(history.jsonl, eval-lessons.json, evals.json)를 읽기 전용으로 서빙: `GET /evals` API, 채팅 헤더의 신뢰 배지(✅ 0.88 · 2 runs), 클릭 시 열리는 `/evals/ui` 간이 대시보드(추세·실행별 표·what-to-fix·active lessons). |
| **Function/UX Effect** | 채팅 화면에서 검증 상태가 항상 보이고, 배지 클릭 한 번으로 rate 추세와 실패별 조치 지침까지 확인 — 터미널 없이. |
| **Core Value** | eval 하네스의 신뢰 신호를 실제 사용자 접점(웹)까지 배달 — 검증이 개발자만의 정보가 아니게 된다. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | eval 결과가 CLI/파일에만 있어 웹 사용자는 에이전트의 검증 상태를 볼 수 없음 |
| **WHO** | 채팅 UI 사용자, 비개발 이해관계자, 배포 확인하는 개발자 |
| **RISK** | 실패 사유에 담긴 태스크 문장 노출(접근 제어: 공개 선택 — 로컬/내부 도구 전제, /info와 동일 수준) |
| **SUCCESS** | 브라우저에서 배지 → 대시보드로 rate·추세·실패 조치·lessons 확인 가능, 신규 쓰기 경로 0 |
| **SCOPE** | 읽기 전용 3종(API/배지/UI 페이지)만 — eval 실행 트리거·인증·비교 뷰는 후속 |

---

## 1. Overview

### 1.1 Purpose

eval-feedback이 파일로 남기는 데이터를 웹에서 소비 가능하게 한다. 서버는 파일을 읽기만
하고, eval 실행은 여전히 CLI(`any2agent eval`)의 몫이다.

### 1.2 Background

- 선행: self-verification(eval 하네스) + eval-feedback(이력·lessons) 완료 — 데이터 소스 확보
- 사용자 결정: 1차 범위 3종 전부(API+배지+UI), 접근 제어는 공개(로컬/내부 도구 전제)

### 1.3 Related Documents

- 선행: [eval-feedback.plan.md](eval-feedback.plan.md)
- Design: [eval-console.design.md](../../02-design/features/eval-console.design.md)

---

## 2. Scope

### 2.1 In Scope

- [ ] `GET /evals` — 최신 실행 요약 + 이력(tail 20) + active lessons + 태스크 수 (JSON)
- [ ] 채팅 UI 헤더 신뢰 배지 — 최신 rate·상태·실행 수, 없으면 "not evaluated"
- [ ] `GET /evals/ui` — 단일 파일 HTML 대시보드 (추세 스파크라인, 실행 표, what-to-fix, lessons)
- [ ] eval 실행 시 what-to-fix 라인을 이력 엔트리에 포함 (대시보드가 조치 지침을 보여줄 수 있도록)

### 2.2 Out of Scope

- eval 실행 트리거 버튼 (서버는 읽기 전용 유지)
- 인증/토큰 게이트 (공개 선택 — 후속에 옵션 추가 가능)
- A/B 비교 뷰 (tool-consolidation의 --compare와 함께)

---

## 3. Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `GET /evals`: history(tail 20)+trend+lessons+최신 실행 요약, 데이터 없으면 `{evaluated:false}` | High | Pending |
| FR-02 | history 엔트리에 실패별 fix 라인 포함(`fixes` 필드) — lessons build 결과 재사용 | High | Pending |
| FR-03 | 채팅 헤더 배지: `✅ 0.88`/`❌ 0.50`/`— not evaluated`, 클릭 시 /evals/ui 새 탭 | High | Pending |
| FR-04 | `/evals/ui`: chat.html과 동일 스타일의 단일 파일 페이지, /evals fetch 렌더 | High | Pending |
| FR-05 | 서버 쓰기 경로 추가 0 — 모든 신규 엔드포인트 GET only | High | Pending |

**Non-Functional**: 신규 의존성 0, 데이터 파일 없거나 파손 시에도 200 + `evaluated:false`(500 금지),
lessons 로드는 기동 시 1회가 아니라 요청 시 재읽기(eval이 서버 기동 후 갱신돼도 반영).

---

## 4. Success Criteria

- [ ] eval 2회 실행 후 브라우저에서 배지에 최신 rate 표시
- [ ] /evals/ui에서 추세·실행 표·what-to-fix·lessons 확인 (Playwright 검증)
- [ ] eval 데이터가 전혀 없는 프로젝트에서도 UI가 "not evaluated"로 정상 렌더
- [ ] 전체 pytest 통과 (기존 34 + 신규)

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 실패 사유·태스크 문장 노출 | Low | High | 사용자 결정: 공개(로컬/내부 전제). guidance 문장만 노출, 트랜스크립트·args 원문은 애초에 저장 안 함 |
| 서버 기동 후 eval 실행 시 stale 데이터 | Medium | High | /evals가 요청 시마다 파일 재읽기 (기동 캐시 금지) |
| history 무한 성장으로 응답 비대 | Low | Low | tail 20 고정 |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change |
|----------|------|--------|
| `server/app.py` | API | GET /evals, GET /evals/ui 추가 (기존 라우트 무변경) |
| `server/web/chat.html` | UI | 헤더에 배지 1개 추가 |
| `evals/history.py` | Module | append에 `fixes` 필드 추가 |
| `server/web/evals.html` | 신규 | 대시보드 페이지 |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| history.jsonl | READ | `cli.cmd_eval --history` | None (필드 추가는 하위 호환 — load는 dict 그대로 반환) |
| history.append | CALL | `cli.cmd_eval`, `connect._eval_gate` | Needs verification (`fixes` 인자 추가 시 시그니처) |
| eval-lessons.json | READ | `server build_app`(주입), `cli`(merge_save) | None (읽기만 추가) |
| chat.html | SERVE | `GET /` | None (배지는 기존 /info 패턴처럼 fetch) |

### 6.3 Verification

- [x] history 필드 추가는 기존 load/trend에 무영향 (dict passthrough)
- [ ] append 시그니처 변경 시 두 호출부(cli, connect) 동시 갱신 — 테스트로 고정

---

## 7. Architecture Considerations

기존 구조 유지 (Python + FastAPI + 단일 파일 HTML). 프레임워크 표는 해당 없음 —
chat.html과 동일한 no-build 바닐라 JS. 서버는 읽기 전용 원칙: eval 실행·수정 경로 없음.

---

## 8. Next Steps

1. [x] Design 문서
2. [ ] 구현 → 브라우저 검증(Playwright) → `/pdca analyze eval-console`
