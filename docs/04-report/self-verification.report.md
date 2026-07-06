# self-verification — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: task-based self-verification (eval) harness
> **PDCA Cycle**: 2026-07-05 ~ 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../01-plan/features/self-verification.plan.md) · [design](../02-design/features/self-verification.design.md) · [analysis](../03-analysis/self-verification.analysis.md)
> **Commits**: `fd0715a` (harness) · `f3aa241` (gitignore eval artifacts)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | connect의 4개 critic(coverage/accuracy/liveness/agent_e2e)은 "도구가 존재·구조·선택되는가"까지만 검증 — **에이전트가 실제 멀티스텝 태스크를 완수하는가**는 아무도 측정 안 함 (Anthropic "Writing tools for agents"의 핵심 권장 미이행). |
| **Solution** | `any2agent/evals/` 패키지: toolspec → 멀티스텝 태스크 자동 생성(`tasks.py`) → 실제 `run_chat` 루프로 라이브 실행(`runner.py`) → 결정적 체크 + LLM-judge 이중 채점(`grader.py`) → 5번째 critic `verifier.task_eval`로 게이트. 실패 트랜스크립트는 기존 `enrich`/`synth_params` repair로 피드백. |
| **Function/UX Effect** | `any2agent eval` 한 명령으로 태스크 성공률·도구 오선택률·에러율이 수치화. CI 친화 exit code(0/1/2). 신규 서드파티 의존성 **0**. 저장소 최초의 pytest 스위트 도입(**24 tests**). |
| **Value Delivered** | 도구 품질 개선이 "감"이 아니라 **측정 기반**이 됨. Gap 분석 **92.0% → ~99%**. 이후 모든 feature(tool-consolidation/composition/response-shaping)가 이 하네스의 `eval --compare`로 채택을 검증하는 토대가 됨. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| 검증 가능한 채점 우선, LLM-judge는 보조 | ✅ | grader가 결정적 체크(tool_called/state/answer_contains/no_errors) 후에만 judge 호출 — 무키 환경에서도 부분 채점 가능 |
| `core/agent.py` 최소 침습 (auto_confirm 분기 ±6 LOC) | ✅ | 러너는 기존 제너레이터 이벤트 스트림을 소비하는 별도 모듈로 격리, agent 수정 1곳 |
| 사람이 curation 가능한 eval set (`<project>.evals.json`) | ✅ | `--regen` 없으면 기존 파일 우선 — 자동 생성은 시작점, 팀 curation이 회귀 자산 |
| eval 전용 독립 budget (전역 상태 공유 회피) | ✅ | `evals/budget.py` 독립 카운터. **단, 1단위 = 태스크-런(내부 run_chat은 MAX_STEPS로 별도 상한)** — §5.2 갭 참조 |
| write 안전: read-only 기본 + `--live-write` 이중 동의 + `[a2a-eval]` 마커 + cleanup | ✅ | danger(DELETE)는 cleanup 외 생성·로드에서 차단, cleanup 실패는 `residue`로 정직 보고 |

## 3. Success Criteria — Final Status (plan §4)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | `eval --project notes-api`가 성공률 리포트 출력 | ✅ | `cli.cmd_eval` + `_print_report`; 통합 테스트 |
| SC-2 | 임계치 미달 시 exit 1 + 실패 태스크별 원인 | ✅ | `cli.py` exit code 0/1/2, per-task reasons |
| SC-3 | 실패 → repair → 재평가 사이클이 `connect --eval`에서 1회 이상 | ✅ | `connect._eval_gate` 2-attempt (enrich/synth_params 매핑) |
| SC-4 | pytest 신설: grader 단위 + notes-api 통합 | ✅ | `test_grader.py`, `test_integration_notes_api.py` |
| SC-5 | 기존 4 critic + CLI 하위 호환 | ✅ | `run_all(eval_tasks=None)` 무영향 회귀 테스트 |

**성공률: 5/5 (100%)** — 단, SC-1·SC-3의 라이브-LLM E2E 슬라이스는 프로바이더 키 부재로 **환경 제약**(코드 완성, CI-with-keys 몫).

## 4. Gap-Analysis Summary

- **초기 92.0%** `(64 full + 10 partial + 1 miss)/75` — FR 8/8, Security §7 5/5, Conventions 100%.
- **동일 세션 수정 → ~99%** (tests 23→24):
  - `--judge-model` **누락(miss)** → CLI 플래그부터 `_judge`까지 end-to-end 구현.
  - `skipped_budget`이 `infra_errors`에 섞여 **오분류** → 별도 필드로 분리(CI가 "예산 소진" vs "인프라 장애" 구분) + 회귀 테스트.
  - budget 세분화(태스크-런 vs 내부 호출) → 설계 §5.2를 per-run 상한으로 doc-sync(코드가 정본).
  - dev 1/2/5/6/8(rounds/write_blocked:str/metrics keys/시그니처/judge-trigger) → 설계를 코드 기준으로 doc-sync.

## 5. Lessons Learned

- **Mock-vs-real-key 맹점**: 러너·게이트는 코드 완성이나 라이브-LLM E2E는 키 없는 환경에서 실행 불가 — "구현됨"과 "라이브 통과함"을 리포트에서 구분해야 함(정직 원칙). 이 맹점은 후속 eval-feedback에서 실제로 물림(`dbab808` 참조).
- **설계 자기모순을 갭 분석이 잡음**: `classify`/시그니처, budget 문구가 다이어그램 §과 본문 §에서 상충 — 코드가 정본이라는 doc-sync 규율이 유효.
- **최소 침습이 값짐**: agent.py 6줄 수정으로 헤드리스 실행을 얻어, 이후 telemetry·composite가 같은 dispatch 경로에 무리 없이 얹힘.

## 6. Remaining / Deferred

- 자동화된 라이브-LLM `eval` 슬라이스 (CI-with-keys) — 환경 제약.
- 런타임 텔레메트리 / 응답 셰이핑 / 도구 통합·명명 — 계획상 **후속 feature로 분리**, 이후 전부 착수·완료됨.
