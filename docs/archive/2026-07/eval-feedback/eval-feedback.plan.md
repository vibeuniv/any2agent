# eval-feedback Planning Document

> **Summary**: eval 결과의 사용자 노출 최소 구성(이력·선별 정보) + 실패 → 원인 분류 → 자동 수정/런타임 지침으로 재발 방지
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-05
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | eval 결과가 일회성 터미널 출력뿐이라 추세를 볼 수 없고, 실패 사유가 원시 문자열이라 사용자가 "그래서 뭘 고치나"를 알 수 없다. 같은 실수가 다음 실행·다음 대화에서 반복된다. |
| **Solution** | (1) 실행마다 요약을 이력(jsonl)으로 남기고 추세를 한 줄로 표시, (2) 실패를 5개 원인으로 분류해 "무엇을·왜·어떻게"만 선별 출력, (3) 실패에서 지침(lesson)을 생성해 `--fix`로 도구를 즉시 수정하거나 서빙 에이전트의 시스템 노트로 주입해 재발을 막는다. |
| **Function/UX Effect** | `eval` 한 번에 "지난번 대비 추세 + 실패별 조치 1줄"이 나오고, 수정 불가한 실수는 에이전트가 대화 중에 지침으로 회피한다. 웹 콘솔 없이 최소 구성. |
| **Core Value** | 평가가 기록·학습되는 자산이 된다 — 실패할 때마다 시스템이 한 단계씩 덜 실수하는 방향으로 수렴. |

---

## 1. Scope

### In Scope (최소 구성)

- [ ] 이력: 실행 요약을 `.any2agent-state/<project>/eval-history.jsonl`에 append, `--history`로 조회, 직전 대비 추세 표시
- [ ] 선별 출력: 실패 태스크당 {원인 분류, 조치 지침 1줄}만 — 원시 reasons는 `--json`으로만
- [ ] 실패 분류기: wrong_tool / bad_args / tool_error / state_mismatch / answer_gap
- [ ] 지침(lessons): 실패 → 결정적 템플릿으로 생성, `<project>.eval-lessons.json` 영속화, 통과 시 해당 태스크 lesson 자동 제거
- [ ] 런타임 주입: serve 시 lessons를 시스템 노트로 주입해 같은 실수 회피 (아티클의 "steer agents with helpful instructions")
- [ ] `eval --fix`: 자동 수정 가능한 실패(wrong_tool/bad_args)에 기존 repair 채널 즉시 적용 + toolspec 저장

### Out of Scope

- 웹 대시보드/HTML 콘솔 (후속 eval-console)
- LLM으로 lesson 문구 다듬기 (결정적 템플릿으로 시작)

## 2. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | 이력 append/조회(`--history`) + 직전 rate 대비 추세 한 줄 | High |
| FR-02 | 실패 태스크 선별 출력: 분류 + 조치 지침 1줄 | High |
| FR-03 | 실패 분류기 5종 (결정적, EvalResult 기반) | High |
| FR-04 | lessons 생성·영속화·자동 정리(통과 시 제거, 상한 20) | High |
| FR-05 | serve 런타임에 lessons 시스템 노트 주입 (파일 있을 때만, 무설정) | Medium |
| FR-06 | `--fix`: `_eval_repair` 재사용 + toolspec 저장 + 재평가 안내 | Medium |

## 3. Success Criteria

- [ ] `eval` 2회 실행 시 두 번째 출력에 추세(▲/▼) 표시
- [ ] 실패 시 태스크당 정확히 1줄의 조치 지침 출력
- [ ] lessons 파일 존재 시 `/chat` 시스템 노트에 지침 포함 (테스트로 고정)
- [ ] `--fix` 후 toolspec 변경 저장 확인
- [ ] 기존 테스트 25개 무파손

## 4. Risks

| Risk | Mitigation |
|------|------------|
| lessons가 오래돼 현재 toolset과 불일치 | 로드 시 참조 도구명 검증, 통과 시 자동 제거, 상한 20 |
| 지침 주입이 프롬프트 오염(정책 격상) | memory와 동일 원칙 — 도구 선택 힌트만, confirm/auth 게이트는 절대 불변 |
| 이력 파일 무한 성장 | jsonl 1줄/실행 — 부담 없음, 조회는 tail N |

## 5. Next Steps

1. [x] Design: [eval-feedback.design.md](eval-feedback.design.md)
2. [ ] 구현 → `/pdca analyze eval-feedback`
