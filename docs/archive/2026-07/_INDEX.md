# Completion Reports — any2agent

7개 feature의 PDCA 완료 보고. 작업 기간 **2026-07-05 ~ 2026-07-06**, 최종 **105 passing tests**, 전부 main 반영.
공통 테마: Anthropic *"Writing tools for agents"* 가이드 대응 — 자동 생성 에이전트에 **측정·학습·상시 신호**를 내재화.

| # | Feature | 한 줄 성과 | Gap % (초기 → 수정 후) | Report |
|:-:|---|---|:--:|---|
| 1 | **self-verification** | 태스크 기반 eval 하네스(생성·실행·이중 채점·`task_eval` 5번째 critic·CLI) — 도구 품질을 감이 아닌 수치로 | 92.0% → ~99% | [report](self-verification/self-verification.report.md) |
| 2 | **eval-feedback** | 이력·추세 + 5분류 실패 분류 + lessons(serve 주입·자기정리) + `eval --fix` | 95.6% → ~99% | [report](eval-feedback/eval-feedback.report.md) |
| 3 | **eval-console** | 읽기 전용 `GET /evals` + 채팅 신뢰 배지 + `/evals/ui` 대시보드(후속 v2·telemetry·help 진화) | 96.4% *(사후 분석)* | [report](eval-console/eval-console.report.md) |
| 4 | **tool-consolidation** (P1) | 결정적 셰이핑 — `resource_action` 재명명 + alias 호환 + list→search 승격 + `eval --compare` | 98.5% → ~100% | [report](tool-consolidation/tool-consolidation.report.md) |
| 5 | **tool-composition** (P2) | 합성 도구 — LLM 제안(필수 승인) + `$input/$steps` 실행기 + 정직한 부분 실패 + 플래그 MAX 상속 | 96.8% | [report](tool-composition/tool-composition.report.md) |
| 6 | **response-shaping** | 구조 인지 절단(`_meta.truncated`) + `response_format` concise/detailed + actionable 에러 힌트(404 형제 제안) | 94.4% → ~100% | [report](response-shaping/response-shaping.report.md) |
| 7 | **runtime-telemetry** | 도구 호출 jsonl(닫힌 스키마·no-raise·로테이션) + 드리프트 의심 도구(자기해제) + Live-usage 카드 + 배지 `⚠` | 88% → ~100% | [report](runtime-telemetry/runtime-telemetry.report.md) |

## 읽는 순서

세로로 읽으면 로드맵이 보인다: **① 측정 수단(eval)** → **② 학습(lessons)** → **③ 웹 노출(console)** → **④⑤ 도구 재설계(shaping·composite)** → **⑥ 응답 효율(shaping)** → **⑦ 상시 신호(telemetry)**. ④·⑤는 ①의 `eval --compare`로 채택을 검증했고, ⑦은 ③의 콘솔을 런타임 신호로 확장한다.

## 관련 문서

- 각 feature 폴더에 plan/design/analysis/report 전체가 함께 아카이브됨 (예: [`self-verification/`](self-verification/))
- 사용자 가이드: [`docs/USAGE.md`](../../USAGE.md) · eval 원리 평문 설명: [`docs/HOW-EVAL-WORKS.md`](../../HOW-EVAL-WORKS.md)

## 정직 보고 (전체 잔여 테마)

- **Mock-vs-real-key 맹점**: 라이브-LLM E2E는 키 없는 CI에서 실행 불가 — self-verification이 예고했고 eval-feedback에서 실제로 물림(`dbab808` multi-tool lesson 드롭 버그).
- **크로스-feature 회귀**: 단일 feature 검증을 통과해도 데이터 흐름이 겹치면 깨짐 — composite×response-shaping 회귀를 갭 분석이 잡음(`58b4988`).
- **eval-console만 원 사이클 갭 분석 부재** — 본 배치에서 사후 보정(96.4%).
