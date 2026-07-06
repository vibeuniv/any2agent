# tool-consolidation Planning Document

> **Summary**: 엔드포인트 1:1 래핑을 벗어나 에이전트 친화적 도구 설계로 — 리소스 기반 명명, list→search 승격, 워크플로 합성, eval 기반 채택 검증
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-05
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 스캐너가 1 라우트 = 1 도구를 기계적으로 생성한다(`get__notes` 같은 동사 기반 이름, list 도구의 전체 목록 반환). Anthropic 가이드의 핵심 원칙 — "API 엔드포인트를 그대로 래핑하지 말라, 에이전트는 컨텍스트가 비싸다" — 를 정면으로 위반하는 구조다. |
| **Solution** | 스캔과 verify 사이에 **도구 셰이핑 단계**를 추가한다: (1) 리소스 기반 재명명·네임스페이싱(결정적), (2) list→search 승격, (3) 자주 연쇄되는 호출의 LLM 합성 도구 제안, (4) 기존 이름 alias로 하위 호환. 채택 여부는 self-verification eval로 A/B 측정해 결정한다. |
| **Function/UX Effect** | 에이전트가 `notes_search`·`notes_get`처럼 의도가 드러나는 도구를 쓰게 되어 도구 오선택·컨텍스트 낭비가 줄고, 멀티스텝 작업이 합성 도구 1회 호출로 단축된다. 기존 toolspec/evals는 alias로 깨지지 않는다. |
| **Core Value** | "자동 생성"이라는 제품 가치를 유지하면서 생성물의 품질을 사람이 설계한 도구 수준으로 끌어올린다 — 그리고 그 개선을 감이 아니라 eval 수치로 증명한다. |

---

## 1. Overview

### 1.1 Purpose

스캐너 산출물(1:1 래핑 도구)을 에이전트 친화적 도구 세트로 변환하는 셰이핑 파이프라인을
만들고, 변환 전/후를 동일 eval 태스크 세트로 비교해 개선을 수치로 검증한다.

### 1.2 Background

- Anthropic "Writing tools for agents": `list_contacts`가 아니라 `search_contacts`,
  `schedule_event`(가용시간 조회+예약 합성), 명확한 네임스페이싱, "사람이 업무를 나누는
  단위"로 도구를 설계하라
- 현재 `scan/code.py`는 `<method>_<path>` 기계 변환(선행 `/` 때문에 `get__notes` 이중
  언더스코어), `scan/openapi.py`는 operationId 우선 — 둘 다 리소스 의미가 이름에 없음
- self-verification(eval 하네스)이 완성되어(Match ~99%) 도구 재설계의 효과를 완수율·
  호출 수로 측정할 수단이 확보됨 — 본 feature의 전제 조건

### 1.3 Related Documents

- 참고: https://www.anthropic.com/engineering/writing-tools-for-agents
- 선행 feature: [self-verification.plan.md](../self-verification/self-verification.plan.md) (eval 하네스)
- Design: [tool-consolidation.design.md](tool-consolidation.design.md) (작성 예정)

---

## 2. Scope

### 2.1 In Scope — Phase 1: 결정적 셰이핑 (사용자 확정 2026-07-06)

- [ ] **셰이핑 단계**: 스캔 직후·verify 이전에 실행되는 결정적 도구 변환 패스 (connect 통합, opt-out 가능)
- [ ] **리소스 기반 재명명** (결정적): `get__notes`→`notes_list`, `get__notes_note_id`→`notes_get`,
      `post__notes`→`notes_create` — 리소스 접두사 네임스페이싱, 충돌·비정형 시 기존 이름 유지(보수적 폴백)
- [ ] **alias 하위 호환**: 기존 이름을 `ToolSpec.aliases`로 보존, dispatch/검증/evals/lessons 참조 해석
- [ ] **list→search 승격** (결정적): 파라미터 없는 컬렉션 read 도구에 limit 파라미터 승격 +
      "전체 목록 대신 필터/검색" 설명 유도 (LLM 파라미터 제안은 기존 synth_params 채널 재사용)
- [ ] **eval A/B 게이트**: `eval --compare <old.toolspec.json>` — 두 toolset을 동일 태스크로 실행,
      완수율 non-inferior + 호출 수 비교 리포트

### 2.2 Out of Scope (후속 사이클)

- **워크플로 합성(composite) 도구** — Phase 2: eval A/B로 Phase 1 효과 검증 후,
  eval 트랜스크립트의 도구 연쇄 데이터를 근거로 착수 (다중 backing 실행기·부분 실패 처리 포함)
- 응답 셰이핑(`response_format` concise/detailed, 필드 필터링) — 별도 feature
- 런타임 텔레메트리·드리프트 감지
- toolrag 검색 고도화(임베딩)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 리소스 기반 재명명 변환기(결정적): path 구조에서 리소스·동작 추출, `<resource>_<action>` 명명, 충돌 시 numeric suffix 아닌 경로 세그먼트 확장 | High | Pending |
| FR-02 | `ToolSpec.aliases` 필드 + dispatch/`by_name()`에서 alias 해석 — 기존 toolspec·evals 참조 무파손 | High | Pending |
| FR-03 | list→search 승격: 배열 반환 read 도구에 필터/limit 파라미터 승격 + 설명에 "전체 목록 대신 검색" 유도 | High | Pending |
| FR-04 | LLM 합성 도구 제안: toolspec + eval 트랜스크립트(도구 연쇄 빈도)에서 composite 후보 생성, 인터랙티브 승인 | Medium | Pending |
| FR-05 | composite 실행기: backing이 다중 call 시퀀스인 도구의 순차 실행·중간값 바인딩, write/danger 플래그는 구성 호출의 최대치 상속(확인 게이트 유지) | Medium | Pending |
| FR-06 | eval A/B 비교: `any2agent eval --compare old.toolspec.json` — 두 toolset을 같은 태스크로 실행, 완수율·호출수·오선택률 비교 리포트 | High | Pending |
| FR-07 | 마이그레이션 커맨드: 기존 toolspec/evals.json의 도구명을 새 이름으로 갱신(alias 매핑 기반, dry-run 지원) | Medium | Pending |
| FR-08 | 셰이핑 결과 메타 기록: toolspec.meta에 naming 버전·alias 매핑·합성 이력 저장 (재실행 멱등성) | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 하위 호환 | 기존 toolspec 로드·서빙·검증 전부 무변경 동작 | 회귀 테스트 (기존 24개 + 신규) |
| 안전 | composite에 write 포함 시 confirm 게이트 유지·강화 (부분 실행 롤백 정책 명시) | 단위 테스트 + 코드 리뷰 |
| 무LLM 동작 | 재명명·alias·(스키마 기반) search 승격은 키 없이 동작; LLM은 합성 제안에만 | notes-api 테스트 |
| 채택 기준 | eval 완수율 셰이핑 후 ≥ 이전 (non-inferior), 평균 도구 호출 수 감소 | FR-06 비교 리포트 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] notes-api에서 `notes_list`/`notes_get`/`notes_create`/`notes_delete` 형태 이름 생성
- [ ] 기존 이름(`get__notes` 등)으로 저장된 evals.json이 alias 해석으로 그대로 동작
- [ ] `eval --compare`가 전/후 완수율·호출수 비교 리포트 출력
- [ ] composite 도구 1개 이상이 notes-api 데모에서 동작 (목록→상세 합성)
- [ ] 전체 pytest 통과 (기존 24 + 신규)

### 4.2 Quality Criteria

- [ ] eval A/B에서 완수율 non-inferior 확인 후에만 기본 활성화
- [ ] 기존 CLI 플래그·아티팩트 포맷 하위 호환

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 이름 변경이 기존 사용자 toolspec/evals를 깨뜨림 | High | High | alias 필드 + 해석 레이어(FR-02), 마이그레이션 dry-run(FR-07), 셰이핑은 opt-out 가능 |
| composite 도구의 부분 실패(1번 호출 성공, 2번 실패)로 상태 불일치 | High | Medium | write 포함 composite는 읽기-선행 설계 강제, 부분 실패 시 수행된 호출 목록을 결과에 정직 보고 |
| LLM 합성 제안이 비현실적/위험한 체인 생성 | Medium | Medium | 인터랙티브 승인 필수(자동 채택 금지), danger 도구 합성 금지, eval로 사후 검증 |
| 재명명 휴리스틱이 비정형 path에서 이상한 이름 생성 | Medium | Medium | 충돌·비정형 감지 시 기존 이름 유지(보수적 폴백), eval e2e로 선택률 확인 |
| eval A/B 비용 (toolset 2벌 × 태스크 세트) | Low | High | 태스크 수 상한(--n) + eval budget 재사용, connect에서는 opt-in |

---

## 6. Architecture Considerations

Python CLI 패키지 기존 구조 유지. 신규 모듈 후보: `any2agent/shape.py`(또는 `shape/`
패키지) — scan 산출물을 입력으로 받아 변환된 ToolSet을 반환하는 순수 패스.
`connect` 파이프라인 위치: scan → **shape** → verify → repair → eval.
composite 실행은 `adapters`가 아니라 `dispatch` 계층 확장으로 (adapter는 단일 호출 유지).
세부 결정(네이밍 규칙 표, composite backing 스키마, alias 해석 지점)은 Design에서.

---

## 7. Convention Prerequisites

기존 코드베이스 관례 준수: snake_case 모듈, dataclass 계약(`spec.py` 스타일), 모듈
docstring에 설계 의도 서술, honest report 원칙, LLM budget 규율. 신규 서드파티 의존성 없음.

---

## 8. Next Steps

1. [ ] Design 문서 작성 (`/pdca design tool-consolidation`) — 네이밍 규칙 표,
       `ToolSpec.aliases`·composite backing 스키마, `eval --compare` 리포트 계약
2. [ ] 구현 → `/pdca analyze tool-consolidation`
3. [ ] (병행 가능) 선행 feature 마감: `/pdca report self-verification`

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-05 | Initial draft | jhchoi |
