# self-verification Planning Document

> **Summary**: toolspec에서 현실적 멀티스텝 태스크를 자동 생성해 실제 에이전트 루프로 실행·채점하는 자체 검증(eval) 시스템
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-05
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | connect의 4개 critic(coverage/accuracy/liveness/agent_e2e)은 "도구가 존재하고 구조가 맞고 선택되는가"까지만 검증한다. **에이전트가 도구들로 실제 사용자 태스크를 완수하는가**는 어디서도 측정하지 않는다 (Anthropic "Writing tools for agents"의 핵심 권장: 현실적 태스크 기반 평가). |
| **Solution** | toolspec에서 멀티스텝 태스크를 자동 생성(`evals/tasks.py`) → 실제 `core/agent.run_chat` 루프로 라이브 실행(`evals/runner.py`) → 상태 검증 + LLM-judge 이중 채점(`evals/grader.py`) → 5번째 critic `task_eval`로 게이트. 실패 트랜스크립트는 기존 repair(`enrich`/`synth_params`)로 피드백. |
| **Function/UX Effect** | `any2agent eval` 한 명령으로 태스크 성공률·도구 오선택률·에러 복구율이 수치로 나온다. connect는 `--eval`로 최종 게이트에 통합. CI에서 exit code로 사용 가능. |
| **Core Value** | 아티클의 "평가 → 에이전트가 도구를 최적화" 루프를 제품에 내재화. 도구 품질 개선(설명·스키마·응답 셰이핑)이 감이 아닌 **측정 기반**이 된다. |

---

## 1. Overview

### 1.1 Purpose

any2agent이 생성한 tool set이 실제 사용자 태스크를 완수할 수 있는지 end-to-end로 측정하고,
실패를 기존 verify→repair 루프에 피드백하는 자체 검증 시스템을 만든다.

### 1.2 Background

- 현재 `agent_e2e`(verifier.py:97)는 도구 **선택률**만 측정 — 실행도, 결과 채점도 없음
- 저장소에 테스트 스위트·evals 하네스가 전무
- Anthropic 엔지니어링 아티클: "여러 도구 호출이 필요한 현실적 태스크로 평가하고,
  성공률·호출 수·토큰·에러를 추적하며, 트랜스크립트를 에이전트에게 넘겨 도구를 최적화하라"

### 1.3 Related Documents

- 참고: https://www.anthropic.com/engineering/writing-tools-for-agents
- Design: [self-verification.design.md](../../02-design/features/self-verification.design.md)

---

## 2. Scope

### 2.1 In Scope

- [ ] `any2agent/evals/` 패키지: 태스크 생성 / 러너 / 그레이더
- [ ] 태스크 세트 영속화 `<project>.evals.json` (사용자가 수동 curation 가능)
- [ ] 5번째 critic `verifier.task_eval` + 게이트 임계치
- [ ] CLI `any2agent eval` (독립 실행, CI 친화적 exit code)
- [ ] connect `--eval` opt-in 최종 게이트 + 실패 트랜스크립트 → repair 피드백
- [ ] write 태스크 안전장치 (opt-in, auto-confirm, cleanup, eval 마커)
- [ ] examples/notes-api 대상 통합 테스트 + grader 단위 테스트 (pytest 도입)

### 2.2 Out of Scope (후속 feature)

- 런타임 텔레메트리(도구 호출 로그·드리프트 감지) — eval 하네스가 자리잡은 뒤
- 응답 셰이핑(`response_format`, 페이지네이션) — eval로 효과 측정 가능해진 뒤 진행
- 도구 통합·명명 재설계(`notes_list` 등) — toolspec 하위 호환 검토 필요

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | toolspec에서 멀티스텝 태스크 자동 생성 (LLM + 키 없을 때 결정적 폴백) | High | Pending |
| FR-02 | 실제 `run_chat` 루프로 태스크 실행, 트랜스크립트(도구 호출·에러·스텝) 수집 | High | Pending |
| FR-03 | 이중 채점: 검증 가능한 체크(도구 호출·상태 재조회·답변 포함) + LLM-judge | High | Pending |
| FR-04 | `task_eval` critic: 성공률 ≥ 임계치(기본 0.8) 게이트 | High | Pending |
| FR-05 | CLI `any2agent eval` — 리포트 출력, `--json`, exit code | High | Pending |
| FR-06 | 실패 트랜스크립트를 `enrich`/`synth_params` 입력으로 피드백 | Medium | Pending |
| FR-07 | write 태스크: `--live-write` opt-in + auto-confirm + cleanup 훅 | Medium | Pending |
| FR-08 | connect에 `--eval` 최종 게이트 통합 | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 안전 | 기본 read-only; write는 명시 동의 + 마커 + cleanup | 코드 리뷰 + 통합 테스트 |
| 비용 | eval 전용 LLM 호출 예산(기본 40회)으로 폭주 방지 | `llm_repair` budget 패턴 재사용 |
| 무LLM 동작 | 키 없이도 결정적 태스크 + 체크 기반 채점으로 부분 동작 | notes-api 통합 테스트 |
| 의존성 | 신규 서드파티 의존성 0 (기존 litellm/httpx 범위 내) | pyproject 확인 |

---

## 4. Success Criteria

- [ ] `any2agent eval --project notes-api`가 examples/notes-api에서 태스크 성공률 리포트를 출력
- [ ] 임계치 미달 시 exit code 1 + 실패 태스크별 원인 요약
- [ ] 실패 → repair → 재평가 사이클이 connect `--eval`에서 1회 이상 동작
- [ ] pytest 스위트 신설: grader 단위 테스트 + notes-api 통합 테스트 통과
- [ ] 기존 4개 critic 동작·기존 CLI 하위 호환 유지

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| LLM 생성 태스크가 비현실적/채점 불가 | High | Medium | 태스크를 파일로 영속화해 사람이 curation; 체크 없는 태스크는 judge로만 채점하되 rate 가중치 낮춤 |
| write 태스크가 실데이터 오염 | High | Low | 기본 read-only, `--live-write` 명시 동의, `[a2a-eval]` 마커, cleanup 훅, 실패 시 잔여물 보고 |
| judge 채점 불안정(비결정성) | Medium | Medium | 검증 가능한 체크 우선, judge는 보조; 동일 태스크 재실행 시 트랜스크립트 비교 리포트 |
| eval LLM 비용 폭주 | Medium | Low | 전용 call budget(40) + 태스크 수 상한(기본 8) |
| run_chat의 confirm 턴 종료와 러너 충돌 | Medium | High | ctx `auto_confirm` 플래그로 dispatch 즉시 실행(설계 4.2 참조) — 최소 침습 수정 |

---

## 6. Architecture Considerations

Python CLI 패키지(기존 구조 유지). 신규 패키지 `any2agent/evals/`는 `verifier`/`connect`와
동일한 층위: `core/agent`(런타임)과 `adapters/rest`(전송)를 소비하고, `llm_repair`와 같은
budget 규율을 따른다. 웹/DB 스택 선택 없음 — 템플릿의 프레임워크 표는 해당 없음.

---

## 7. Next Steps

1. [x] Design 문서 작성 (`self-verification.design.md`)
2. [ ] 구현 (Design §11 Implementation Order)
3. [ ] `/pdca analyze self-verification` 갭 분석

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-05 | Initial draft | jhchoi |
