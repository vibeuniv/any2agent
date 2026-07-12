# eval-stats — Gap Analysis

> **Design**: [eval-stats.design.md](../02-design/features/eval-stats.design.md) · **Analyzed**: 2026-07-12 (gap-detector)

## Match Rate: 93% ✅ (gate 90%)

가중 합: `0.10·Struct(100) + 0.20·Func(98) + 0.20·Contract(96) + 0.25·Intent(90) + 0.15·Behav(95) + 0.10·UX(72) = 92.75%`.

## Load-bearing 검증 (전부 통과)
- (a) `strict=False` 기본 게이트 **불변** (rate≥threshold) — 하위호환 확인
- (b) stats 레퍼런스 값 정확 (Wilson(8,10)=[0.49,0.94], McNemar·베타이항)
- (c) `judge_votes=1` == 기존 단일 draw (무회귀)
- (d) budget × votes 반영, (e) 리포트 키 **추가만**, (f) McNemar 4분기, (g) 평문 설명 콘솔+문서 양쪽

## 유일한 미달 — §7 콘솔 UI 2건 (UX 72%)
| 항목 | 설계 §7 | 실제 |
|---|---|---|
| compare McNemar **카드** | 콘솔 카드로 p·discordant·verdict | 콘솔 help 텍스트 + CLI 데이터 |
| judge **투표 분포** | 콘솔에 "3표 중 2 pass" | 콘솔 help 텍스트 + CLI 데이터 |

## 결정: 카드 미구현, 설계 정정 (ponytail)
두 항목은 `/evals`에 **영속화되지 않는** CLI 고유 데이터다. 콘솔 카드로 만들려면 A/B 비교
결과·per-task judge 결과를 저장하는 **새 데이터 계층**이 필요한데:
- 콘솔은 **상시 eval 건강** 표시용, compare는 **일회성 A/B 결정**(실행→verdict 읽고 결정) — CLI가 적합
- 사용자 요청 범위 밖 (판단·개선의 시각화는 CI 구간·검정력·사후확률로 이미 충족)

→ 설계 §7/SC-4를 "콘솔=**설명**, CLI=**수치**"로 정정. 콘솔은 이 두 개념을 평문으로 설명하고,
실제 compare/judge 수치는 `eval` 출력에 표시(이미 구현). 데이터 계층 신설은 YAGNI, 요청 시 후속.

## 시각화 실제 확인 (브라우저)
CI 구간 텍스트("23%–88% · ⚠ too few tasks"), 차트 whisker, 드리프트 사후확률 바("99% confident"),
"How does this work?" 통계 패널 — 전부 렌더 확인(스크린샷).

## 잔여
- compare/judge를 콘솔 카드로 원할 시 별도 feature(영속화 필요) — 현재는 CLI + 설명으로 충족
- 155 tests, 신규 의존성 0
