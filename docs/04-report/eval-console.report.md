# eval-console — Completion Report

> **Status**: Complete
> **Project**: any2agent · **Feature**: 읽기 전용 `GET /evals` API + 채팅 신뢰 배지 + `/evals/ui` 대시보드
> **PDCA Cycle**: 2026-07-06 · **Author**: docs-scribe (retro)
> **Docs**: [plan](../01-plan/features/eval-console.plan.md) · [design](../02-design/features/eval-console.design.md) · [analysis](../03-analysis/eval-console.analysis.md) *(사후 작성 — 원 사이클엔 부재)*
> **Commits**: `b93e72a` (web eval console) · `45d3aad` (콘솔 v2) · `b4438f0` (help 패널 + telemetry 상시 신호)

## 1. Executive Summary

| Perspective | Content |
|---|---|
| **Problem** | eval 수치·이력·실패 내역이 터미널·파일에만 있어, 브라우저 사용자(채팅 UI 이용자·비개발 이해관계자)는 "이 에이전트가 검증된 상태인가"를 알 방법이 없음. |
| **Solution** | 이미 파일로 쌓이는 데이터(history.jsonl, eval-lessons.json, evals.json)를 **읽기 전용**으로 서빙: `GET /evals`(요청마다 재읽기), 채팅 헤더 신뢰 배지(`✅ 0.88 · 3 runs`), 클릭 시 `/evals/ui` 단일 파일 대시보드(verdict·추세·what-to-fix·lessons). |
| **Function/UX Effect** | 채팅 화면에 검증 상태가 항상 노출, 배지 클릭 한 번으로 rate 추세·실패별 조치까지 — 터미널 없이. **신규 서버 쓰기 경로 0**, 신규 의존성 0. |
| **Value Delivered** | eval 하네스의 신뢰 신호가 실제 사용자 접점(웹)까지 배달됨 — 검증이 개발자만의 정보가 아니게 됨. 이후 v2·telemetry 진화로 "빌더 아닌 누구나 읽는" 콘솔로 발전. Gap 분석(사후) **96.4%**. |

## 2. Key Decisions & Outcomes (from design)

| 결정 (design) | 따랐나 | 결과 |
|---|:--:|---|
| Option A — Minimal Changes (GET 2 + HTML 1 + 배지 fetch), 별도 서비스 레이어 과설계 회피 | ✅ | `app.py`에 `/evals`·`/evals/ui`만 추가, 기존 라우트 무변경 |
| stale 방지: `/evals`가 요청마다 파일 재읽기 (기동 캐시 금지) | ✅ | `test_evals_rereads_files_per_request` — 기동 후 eval 실행이 재시작 없이 반영 |
| `history.append(fixes=None)` 하위 호환 + cli/connect 두 호출부 갱신 | ✅ | `history.py:19`, `cli.py:214`, `connect.py:408` 모두 `fixes=built` |
| 읽기 전용 불변 — 신규 엔드포인트 GET only | ✅ | `/evals`, `/evals/ui` 둘 다 `@app.get`; 무데이터·파손 시 200(500 금지) |
| 접근 제어: 공개(로컬/내부 도구 전제), guidance 문장만 노출·트랜스크립트 비저장 | ✅ | `/info`와 동일 수준 — 사용자 결정대로 |

## 3. Success Criteria — Final Status (plan §4)

| # | 기준 | 상태 | 증거 |
|---|---|:--:|---|
| SC-1 | eval 2회 후 브라우저 배지에 최신 rate | ✅ | `chat.html` 배지 `✅/❌ rate · N runs`, 라이브 확인 |
| SC-2 | `/evals/ui`에서 추세·실행 표·what-to-fix·lessons | ✅ | `evals.html` 4섹션; Playwright는 **수동 검증** |
| SC-3 | eval 데이터 없는 프로젝트에서도 "not evaluated" 정상 렌더 | ✅ | 배지 `— not evaluated`, 대시보드 빈 상태(`evals.html:142`) |
| SC-4 | 전체 pytest 통과 | ✅ | `test_evals_endpoint.py` 5종 포함 회귀 통과 |

**성공률: 4/4 (100%)** — SC-2의 브라우저 자동화만 수동 단계(설계도 "수동 검증"으로 명시).

## 4. Gap-Analysis Summary (사후)

이 feature는 **구현 당시 갭 분석이 없었다** — docs Task #15에서 사후 수행([eval-console.analysis.md](../03-analysis/eval-console.analysis.md)).

- **원설계 대비 96.4%** `(26 full + 2 partial)/28` — FR 5/5, API 계약·UI 2면·에러 처리 전항목 구현, 누락 0. 감점은 엔드포인트 레벨 파손-파일 테스트 공백 + Playwright 미자동화.
- **구현이 원설계보다 앞서 진화** (역방향 추가, 사용자 주도 — 전부 additive·쓰기 경로 0 유지):
  - **v2 UI**(`45d3aad`): 평문 verdict("Working well"/"Needs attention"), KPI 4-타일, 비례 막대 차트(설계 SVG 스파크라인 교체), `task_prompts`로 실패를 사용자 문장으로 표시.
  - **runtime 섹션**(`aea3b1b`, runtime-telemetry): "Live usage" 카드 + 배지 `⚠` — 자체 갭 분석 보유.
  - **help 패널**(`b4438f0`): "How does this work?" 평문 설명(3신호·정직한 분모·lessons·telemetry).

## 5. Lessons Learned

- **갭 분석 누락이 유일한 프로세스 공백**: 7개 feature 중 이것만 원 사이클에서 gap-detector를 안 돌렸다. 다행히 후속 telemetry가 콘솔을 확장하며 자체 분석을 남겨 부분 보정됐고, 본 사후 분석이 나머지를 메움 — 그래도 "구현 직후 갭 분석" 규율이 빠지면 나중에 회수 비용이 든다는 교훈.
- **읽기 전용 원칙의 배당금**: 서버 쓰기 경로 0을 처음부터 못 박아, v2·telemetry가 얹힐 때도 보안 표면이 커지지 않았다.
- **"빌더 아닌 누구나"가 진짜 요구였다**: 원설계의 기술적 표(rate/rated/passed)는 만든 사람만 읽혔고, v2의 평문 verdict·task_prompts가 실사용자 요구였다 — 최소 구성 출시 후 사용자 피드백으로 UI를 진화시킨 것이 옳았다.

## 6. Remaining / Deferred

- `/evals` 파손-파일 계약 테스트 1건 + Playwright 스모크 CI 배선 — 저위험 커버리지 공백.
- eval 실행 트리거 버튼·인증 게이트·A/B 비교 뷰 — 의도적 out of scope(읽기 전용 유지).
