# response-shaping Planning Document

> **Summary**: 도구 응답의 토큰 효율 셰이핑(구조 단위 절단·concise/detailed) + actionable 에러 힌트 — 가이드 대응 마지막 축
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 도구 응답이 raw JSON 그대로 LLM에 유입되고 유일한 방어가 직렬화 후 6000자 절단(`agent.py _tool_msg`) — JSON이 구조 중간에서 잘려 깨진 데이터가 들어간다. 에러는 `http_404`·raw 예외 문자열뿐이라 에이전트가 "무엇을 고쳐야 하는지" 모른다. |
| **Solution** | 응답 렌더 레이어(`respond.py`) 신설: 배열은 아이템 단위로 절단하고 "N/M개 표시 — limit/필터를 쓰라" 유도 문구를 동봉, `response_format`(concise/detailed) 파라미터를 list 도구에 승격, 에러는 상태코드별 actionable 힌트로 변환(404면 리소스 명명 기반 형제 도구 `notes_list`를 제안). |
| **Function/UX Effect** | 에이전트가 항상 온전한(파싱 가능한) 구조를 받고, 실패 시 다음 행동이 힌트로 주어져 자가 복구율이 오른다. 대용량 목록이 컨텍스트를 태우지 않는다. |
| **Core Value** | Anthropic 가이드의 토큰 효율·에러 응답 원칙을 구현 — eval(자가 검증)로 개선을 측정할 수 있는 마지막 큰 갭 해소. |

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | raw JSON + 6000자 무단 절단 + 무의미한 에러 문자열 — 가이드의 토큰 효율·에러 원칙 위반 |
| **WHO** | 생성된 에이전트의 LLM (도구 결과 소비자), 대용량 API를 가진 사용자 |
| **RISK** | 셰이핑이 데이터를 왜곡해 채점(state check)·UI 표시를 깨뜨림 → LLM-facing 메시지만 셰이핑, 원본 이벤트·adapter 결과는 불변 |
| **SUCCESS** | 절단 시에도 항상 유효 JSON + 유도 문구, 에러에 상태별 힌트+형제 도구 제안, 기존 74 테스트 무파손 |
| **SCOPE** | respond.py + agent 루프 통합 + shape.py response_format 승격 — 페이지네이션 커서·필드 프로젝션은 후속 |

## 1. Scope

### In Scope
- [ ] `respond.py`: 구조 인지 절단(배열 아이템 단위, 긴 문자열 마커 절단) + 절단 고지·유도 문구
- [ ] `response_format` 파라미터(concise|detailed): shape.py가 list 도구에 승격, 런타임에 agent 루프가 pop(API로 유출 금지)
- [ ] concise 모드: null/빈 필드 제거 + 배열 기본 절단폭 축소
- [ ] 에러 힌트: 상태코드 클래스별 actionable 문구 + 404 시 형제 read 도구 제안(리소스 명명 활용)
- [ ] `_tool_msg` 교체: 문자 절단 → respond.render (항상 유효 JSON 보장, 상한 유지)

### Out of Scope
- 페이지네이션 커서 자동화, 필드 프로젝션/스키마 학습 (후속)
- adapter 결과·SSE 이벤트·grader state check의 원본 데이터 변경 (불변 유지)

## 2. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | 배열 절단: 최대 아이템 수 초과 시 앞 N개 + `_meta.truncated {shown,total,hint}` | High |
| FR-02 | 문자 상한 초과 시 아이템 수를 점진 축소해 **항상 유효 JSON** 유지 (mid-slice 금지) | High |
| FR-03 | `response_format`: concise(기본, null 제거·좁은 절단폭)/detailed(원형·넓은 폭), list 도구 스키마에 승격 | High |
| FR-04 | response_format은 dispatch 전에 pop — 백엔드 API로 절대 전달 안 됨 | High |
| FR-05 | 에러 힌트: 400/422·401/403·404·405·429·5xx·transport별 문구, 404는 `<resource>_list/search` 형제 제안 | High |
| FR-06 | 원본 불변: adapter 반환값·UI 이벤트·grader가 보는 데이터는 셰이핑 없음 (LLM 메시지만) | High |

## 3. Success Criteria
- [ ] 대형 배열 응답이 절단 고지와 함께 유효 JSON으로 LLM에 전달 (단위 테스트)
- [ ] 404 응답의 LLM 메시지에 형제 도구 제안 포함
- [ ] 기존 74 테스트 무파손 + notes-api 라이브 확인
- [ ] Gap 분석 ≥ 90%

## 4. Risks
| Risk | Mitigation |
|------|------------|
| 셰이핑이 검증 신호 왜곡 | grader/verifier는 adapter 원본 사용 경로 유지 — respond는 `_tool_msg` 전용 |
| 힌트가 실제와 다른 유도(잘못된 형제 제안) | 결정적 규칙만: 동일 리소스 접두사 + list/search 접미사 존재 시에만 제안 |
| concise가 필요한 ID 제거 | null/빈 값만 제거, 값 있는 필드는 보존 (가이드의 "detailed는 후속 호출용 ID 포함"은 detailed로 해소) |
