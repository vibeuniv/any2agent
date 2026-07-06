# eval-feedback — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: eval 이력·추세 + 5분류 실패 분류 + lessons(런타임 주입·자기정리) + `eval --fix`
> **PDCA Cycle**: 2026-07-05 ~ 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../01-plan/features/eval-feedback.plan.md) · [design](../02-design/features/eval-feedback.design.md) · [analysis](../03-analysis/eval-feedback.analysis.md)
> **Commits**: `fd0715a` (모듈 동봉 — history.py/lessons.py) · `f048f7a` (PDCA 문서) · `dbab808` (multi-tool lesson 누락 버그 수정)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | eval 결과가 일회성 터미널 출력뿐 — 추세를 볼 수 없고, 실패 사유가 원시 문자열이라 "그래서 뭘 고치나"를 모름. 같은 실수가 다음 실행·다음 대화에서 반복. |
| **Solution** | (1) 실행마다 요약을 `eval-history.jsonl`에 append + 추세 1줄, (2) 실패를 5개 원인(wrong_tool/bad_args/tool_error/state_mismatch/answer_gap)으로 분류해 "무엇·왜·어떻게" 1줄만 선별 출력, (3) 실패→lesson 생성→`--fix`로 toolspec 즉시 수정하거나 **serve 시 시스템 노트로 주입**해 재발 방지. |
| **Function/UX Effect** | `eval` 한 번에 "지난번 대비 추세 + 실패별 조치 1줄". 수정 불가한 실수는 서빙 에이전트가 대화 중 지침으로 회피. lessons는 통과 시 자동 제거·상한 20으로 자기정리. |
| **Value Delivered** | 평가가 **기록·학습되는 자산**이 됨 — 실패할 때마다 한 단계씩 덜 실수하는 방향으로 수렴. Gap 분석 **95.6% → ~99%**. 웹 콘솔 없는 최소 구성으로 후속 eval-console의 데이터 소스를 확보. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| classify는 결정적 우선순위 (복합 실패 시 wrong_tool 우선) | ✅ | `lessons.classify(result)` — 6단계 우선순위, EvalResult만으로 판정 |
| lesson 문구는 결정적 템플릿 (LLM 다듬기 out of scope) | ✅ | 5분류별 guidance 템플릿; 사용자 출력 1줄 = guidance 동일 문구 재사용 |
| merge_save 자기정리: 통과 태스크 제거 + 도구명 검증 + 상한 20 | ✅ | 통과 시 lesson 제거, 존재 않는 도구 참조 제거, 최신 20 유지 |
| 런타임 주입은 memory 원칙 — 도구 선택 힌트만, confirm/auth 게이트 불변 | ✅ | `run_chat`이 lessons를 시스템 노트로 prepend, 게이트는 lessons를 읽지 않음 |
| 선별 출력: 원시 reasons/args는 `--json`에만, 트랜스크립트 원문은 비저장 | ✅ | §4 노출 기준 표대로 — 신뢰·프라이버시 정합 |

## 3. Success Criteria — Final Status (plan §3)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | `eval` 2회 시 두 번째에 추세(▲/▼) | ✅ | `history.trend_line` (`"rate 0.75 (prev 0.88 ▼0.13, 5 runs)"`) |
| SC-2 | 실패 시 태스크당 정확히 1줄 조치 지침 | ✅ | `cmd_eval` "what to fix" 선별 출력 |
| SC-3 | lessons 파일 존재 시 `/chat` 시스템 노트에 지침 (테스트 고정) | ✅ | `agent._inject_lessons` 단위 테스트 (`test_lessons_history.py`) |
| SC-4 | `--fix` 후 toolspec 변경 저장 | ✅ | `_eval_repair` 재사용 + toolspec 저장 + "re-run to confirm" 안내 |
| SC-5 | 기존 테스트 25개 무파손 | ✅ | 회귀 통과 (전체 스위트 지속 성장) |

**성공률: 5/5 (100%)**

## 4. Gap-Analysis Summary

- **초기 95.6%** `(31 full + 3 partial)/34` — FR 6/6, 성공 기준 5/5(1건 코드 존재·테스트 부재), 누락 0.
- **동일 세션 수정 → ~99%**:
  - **D1 헤더 중복**(주입 문구가 `lessons.py`/`agent.py` 두 곳) → `_inject_lessons`가 `lessons.render()` 재사용(lazy import).
  - 파손 lessons 파일 → 감지 시 경고 1줄 출력(설계 §5 충족).
  - `classify` 시그니처·stale 제거 시점 → 설계를 코드 기준으로 정정.
  - 테스트 공백(MAX-20 상한, `--fix` toolspec 저장) → 전용 테스트 추가.

## 5. Lessons Learned

- **Mock 채점의 실채용 맹점 — `dbab808`**: 실키(real provider key) eval 실행에서 **다중 도구를 참조하는 lesson이 stale 필터에 조용히 드롭**되는 버그 발견. stale 판정이 lesson의 참조 도구를 단일 토큰으로 매칭해, 멀티툴 guidance("call notes_create then notes_list")가 유효 도구를 참조함에도 제거됐다. mock 테스트는 이 경로를 재현 못 함 — self-verification 리포트가 예고한 "mock-vs-real-key 맹점"이 실제로 물린 사례. 수정 + 회귀 테스트로 마감.
- **동일 문구 재사용의 이득**: 사용자 출력 1줄과 lesson guidance를 같은 문자열로 두어, 드리프트 없이 "본 것 = 주입되는 것"이 보장됨.
- **feat 커밋이 분리되지 않음(프로세스)**: 본 feature의 `history.py`/`lessons.py`는 self-verification 커밋(`fd0715a`)에 **동봉**되어 별도 `feat: eval-feedback` 커밋이 없다 — PDCA 문서는 존재하나 git 이력만으로는 경계가 흐림. 후속엔 feature당 커밋 분리 권장.

## 6. Remaining / Deferred

- LLM으로 lesson 문구 다듬기 — 의도적 out of scope(결정적 템플릿으로 시작).
- 웹 대시보드 노출 — 후속 eval-console에서 이 데이터를 소비(완료).
