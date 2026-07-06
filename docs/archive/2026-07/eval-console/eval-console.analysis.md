# eval-console — Gap Analysis

> **Feature**: eval-console (읽기 전용 `/evals` API + 신뢰 배지 + `/evals/ui` 대시보드)
> **Design Doc**: [eval-console.design.md](eval-console.design.md)
> **Analyzed**: 2026-07-06 (retro — 이 feature는 구현 당시 갭 분석이 없었다; docs Task #15에서 사후 수행)
> **Note**: 구현이 원설계보다 앞서 진화함 (v2 UI, task_prompts, runtime 섹션, help 패널).
> **원설계 항목에 대해서만** design-vs-implementation을 채점하고, 사후 진화는 역방향 추가로 별도 기술.

## Match Rate: 96.4% ✅

`(26 full × 1.0) + (2 partial × 0.5) + (0 miss) = 27.0 / 28 designed items = 0.964`

FR 5/5 충족, API 계약(§2)·UI 2면(§3)·모듈 변경(§4)·에러 처리(§5) 전항목 구현. 누락 0.
감점은 전부 **테스트 커버리지 경계** — 엔드포인트 레벨 파손-파일 경로와 브라우저 자동화.

## 설계 항목 대조 (원설계 기준)

| 설계 근거 | 항목 | 상태 | 증거 |
|---|---|:--:|---|
| FR-01 / §2 | `GET /evals`: history(tail 20)+trend+lessons+latest, 무데이터 시 `{evaluated:false}` | ✅ | `app.py:118-152`, `test_evals_endpoint.py::test_evals_empty…` |
| FR-01 / §1 | 요청마다 파일 재읽기 (기동 캐시 금지) | ✅ | `app.py:120-126`, `test_evals_rereads_files_per_request` |
| FR-01 / §5 | 무데이터·파손 시 항상 200 (500 금지) | ✅ | `app.py:127-128` (`not entries and not lessons and not runtime`) |
| FR-02 / §2 | history 엔트리에 `fixes` 필드 | ✅ | `history.py:34-36`, `latest.fixes` 왕복 (`test_evals_populated_with_fixes…`) |
| FR-02 / §4 | `append(…, fixes=None)` 하위 호환 + cli/connect 두 호출부 갱신 | ✅ | `history.py:19`, `cli.py:214`, `connect.py:408` (`fixes=built`) |
| FR-03 / §3.1 | 배지 마크업 `<a id="evalBadge" href=… target="_blank">`, 상태 3종 | ✅ | `chat.html:119,197-205` (`✅`/`❌`/`— not evaluated`) |
| FR-03 / §3.1 | 배지 fetch 1회, 실패 시 숨김 (콘솔 에러 금지) | ✅ | `chat.html` fetch 분기 |
| FR-04 / §3.2 | `/evals/ui` 단일 파일, `/evals` fetch 렌더, `{{PROJECT}}` 치환 | ✅ | `app.py:154-157`, `evals.html:137`, `test_evals_ui_serves_dashboard` |
| §3.2-1 | 헤더: 프로젝트 + 최신 상태 + trend | ✅ | `evals.html` hero (진화: 평문 verdict) |
| §3.2-2 | What to fix (실패 시만): task_id·class 칩·guidance | ✅ | `evals.html:198-213` |
| §3.2-3 | History 표(20) + 추세 시각화 | ✅ | `evals.html:184-196` (진화: SVG 스파크라인 → 비례 막대) |
| §3.2-4 | Active lessons + "매 대화 주입" 설명 | ✅ | `evals.html:214-229` |
| §3.2-5 | 무데이터 빈 상태 "not evaluated yet — run …" | ✅ | `evals.html:142` |
| FR-05 | 서버 쓰기 경로 추가 0 (신규 라우트 GET only) | ✅ | `/evals`, `/evals/ui` 둘 다 `@app.get` |
| §4 | `pyproject` package-data (`web/*.html` glob) 무변경 | ✅ | glob이 evals.html 자동 포함 |
| §5 | UI fetch 실패 시 "Failed to load" 1줄 | ✅ | `evals.html:222-223` (`.catch`) |
| §6 | `GET /evals` 데이터 있음/없음 경로 테스트 | ✅ | endpoint 테스트 3종 |
| §6 | `history.append(fixes=)` 하위 호환 테스트 | ✅ | `test_history_append_without_fixes_keeps_old_schema` |
| §6 | **파손 파일 경로 (엔드포인트 레벨)** | ⚠️ | `history.load`가 파손 줄 skip은 별도 테스트됨, `/evals` 레벨 파손 케이스는 미명시 |
| §6 | **Playwright 브라우저 검증** | ⚠️ | 수동 단계로 남음 (자동화 안 됨) — 설계도 "수동 검증 단계"로 표기 |

(위 표는 28개 설계 항목을 그룹으로 압축 표기 — FR 5 + API 계약 8 + UI 10 + 모듈/에러 3 + 테스트 2.)

## Deviations (경미)

- **D1 (trivial)**: 배지 `href="evals/ui"`(상대) vs 설계 `/evals/ui`(절대) — 동작 동일, `target="_blank"` 유지.
- **D2 (Info)**: 설계는 History 시각화를 "인라인 SVG 스파크라인"으로 명시했으나 구현은 **비례 막대 차트**("taller is better") — 의도(추세 가독)는 동일, v2에서 의도적으로 교체.

## 사후 진화 (역방향 추가 — 사용자 주도, 별도 판정)

이 항목들은 원설계 범위(읽기 전용 3종)를 넘어선 추가로, 원설계 채점에서 제외하고 아래에 정직 기술.
전부 **additive이며 서버 쓰기 경로 0(FR-05 불변)**을 유지 — 방향성은 옳다.

| 추가 | 출처 | 판정 |
|---|---|---|
| **eval console v2** — 평문 verdict("Working well"/"Needs attention"), KPI 4-타일, 비례 막대 차트, `task_prompts`로 실패를 사용자 문장으로 표시 | `45d3aad` (ui: eval console v2) | 적절 — "빌더 아닌 누구나 읽는다"는 사용자 요구. 가독성 개선, 신규 위험 없음 |
| **`task_prompts`** 필드 (`/evals`) — 불투명 task_id 대신 원 발화 노출 | `app.py:130-138` | 적절 — 실패의 "무엇을"을 사용자 언어로. endpoint 테스트가 `tasks_total` 경로 커버 |
| **runtime 섹션** — `/evals.runtime` 요약 + "Live usage" 카드 + 배지 `⚠` suspects 병기 | `aea3b1b` (runtime-telemetry) | 별도 feature — 자체 갭 분석 있음: [runtime-telemetry.analysis.md](../runtime-telemetry/runtime-telemetry.analysis.md) (88%→~100%). 콘솔은 그 소비면 |
| **help 패널** "How does this work?" — 3신호·정직한 분모·lessons·telemetry 평문 설명 | `b4438f0` | 적절 — 비개발 이해관계자 대상. 콘솔의 신뢰 목적과 정합 |

## 잔여 (정직 보고)

- 엔드포인트 레벨 파손-파일 테스트는 모듈 레벨(`history.load` skip)로 커버되나 `/evals` 계약 테스트에는 미추가 — 저위험(경로는 실제로 방어됨), 커버리지 공백만.
- 브라우저 자동화(Playwright)는 수동 검증으로 수행됨 — CI 자동화는 후속.
- 이 feature는 **구현 당시 갭 분석이 없었다** — 본 문서가 사후 보정. 이후 telemetry가 콘솔을 확장하며 자체 갭 분석을 남긴 것이 공백을 부분적으로 메웠다.

## Recommended next actions

- `/pdca report eval-console` — 본 사후 분석을 근거로 완료 보고 (docs Task #15 A에 포함).
- 후속(선택): `/evals` 파손-파일 계약 테스트 1건 추가, Playwright 스모크 CI 배선.
