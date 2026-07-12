# eval-stats Design Document

> **Summary**: stdlib 통계 엔진 + opt-in --strict 게이트 + 콘솔 시각화 + 평문 설명
>
> **Project**: any2agent / **Date**: 2026-07-12 / **Status**: Draft
> **Planning Doc**: [eval-stats.plan.md](../../01-plan/features/eval-stats.plan.md) · Context Anchor: plan과 동일

## 1. Architecture (선택: Pragmatic Balance)

```
evals/stats.py  (순수 stdlib math — 정본)
  wilson(k,n)          → (lo, hi)          이항 비율 신뢰구간
  underpowered(k,n)    → bool              rated 부족/구간 과폭 판정
  mcnemar_exact(b,c)   → p                 쌍체 pass/fail 비교
  beta_binom_gt(e,n,p0)→ P(err>p0)         드리프트 사후확률
  vote(passes)         → (pass, agreement) judge k-투표
        │ 소비 (부작용 0)
        ├─ verifier.task_eval : rate_ci, underpowered, strict 게이트
        ├─ grader._judge      : k-투표 + agreement
        ├─ telemetry.summary  : suspect.p_degraded
        └─ cli(eval/compare)  : --strict, --judge-votes, McNemar verdict
                                       │
server /evals → evals.html : 오차막대·검정력배지·McNemar·사후확률바·투표분포 + "How is this judged?" 평문 패널
```

## 2. `evals/stats.py` — 수식과 시그니처 (전부 math만)

- `wilson(k, n, z=1.96) -> (lo, hi)`: Wilson 점수 구간.
  `center=(k+z²/2)/(n+z²)`, `half=z·√(k(n−k)/n+z²/4)/(n+z²)`. n=0 → (0,1).
  소표본에서 Wald보다 정확(경계 근처에서도 [0,1] 유지).
- `underpowered(k, n, min_n=5, max_hw=0.15) -> bool`: `n<min_n` 또는 Wilson 반폭>`max_hw`.
- `mcnemar_exact(b, c) -> float`: 2-sided 정확검정.
  `p = min(1, 2·Σ_{i=0}^{min(b,c)} C(b+c,i)·0.5^(b+c))`. `b+c=0` → 1.0(변화 없음).
- `beta_binom_gt(errors, n, p0=0.5, prior=(1,1)) -> float`: 사후 `Beta(a+e, b+n−e)`에서
  `P(p>p0)=1−I_{p0}(a+e, b+n−e)`. 정규화 불완전베타 `I_x`는 표준 연분수(betacf, ~20줄) stdlib 구현.
- `vote(passes: list[bool]) -> (bool, float)`: 다수결 + agreement(`max(sum,N−sum)/N`).

각 함수에 `ponytail:` 주석으로 근사 한계(예: betacf 수렴 조건) 명시. 모듈 `__main__`에
레퍼런스 값 self-check(assert).

## 3. 게이트 계약 (하위호환)

`task_eval` 리포트에 **추가만**: `rate_ci=[lo,hi]`, `underpowered:bool`, `min_rated`.
- 기본(비-strict): `passed = rate ≥ threshold`(**기존 그대로**).
- `strict=True`: `passed = ci_lo ≥ threshold AND not underpowered AND not residue`.
  실패 시 사유 `"underpowered: n=3<5, add ≥2 tasks"` 또는 `"ci_lo=0.52<0.80"`.
- `run_all`/connect `_eval_gate`는 기본 경로 → 불변.

## 4. judge 투표 (FR-04)

`grader._judge(...)`를 `votes=N`(기본 1) 래핑: N번 표집 → `stats.vote`. 리포트에
`judge={"pass","reason","agreement","n"}`. budget N배 소비. N=1이면 현행과 동일.

## 5. compare McNemar (FR-05)

두 실행의 **태스크별 pass/fail**을 쌍으로 맞춰(같은 task_id):
`b=old_pass&new_fail`, `c=old_fail&new_pass`. `p=mcnemar_exact(b,c)`.
verdict: `b+c < 3` → **inconclusive**("표본 부족, 태스크 늘려라"); else `c>b & p<0.05` → new 우세,
`b>c & p<0.05` → 회귀, 그 외 → 무차이. 기존 avg_tool_calls 비교는 보조로 유지.

## 6. 드리프트 사후확률 (FR-06)

`telemetry.summary`의 각 suspect에 `p_degraded=beta_binom_gt(recent_errors, recent_calls, 0.5)`.
카운트 판정(`recent≥5/10`)은 트리거로 유지, 사후확률은 **신뢰도 표시**로 추가. 웹훅 payload에도 포함.

## 7. 콘솔 시각화 (FR-07, evals.html 확장)

- **rate 오차막대**: 히어로/KPI의 rate에 Wilson 구간을 SVG 수평 오차막대로. underpowered면 회색+배지 "표본 부족 · +N개".
- **compare / judge 수치는 CLI에 표시**(콘솔은 설명만): A/B 비교와 per-task judge 투표는
  `/evals`에 영속화되지 않는 일회성/실행별 데이터라, 콘솔 카드 대신 `eval` 출력에 McNemar
  p·discordant·verdict와 judge 투표/agreement를 표시하고, 콘솔의 "How does this work?"
  패널이 개념을 평문으로 설명한다. (콘솔 카드화는 저장 계층이 필요 — YAGNI, 요청 시 후속.)
- **드리프트**: suspect에 사후확률 바(`p_degraded`)를 "실제로 고장일 확률 82%"로.
- **judge**: 투표 분포(예 "3표 중 2 pass")와 agreement.
- **"How is this judged?" 패널**(신규, 접이식): §9 평문 설명.
- 전부 단일 파일·바닐라 JS·SVG, 신규 의존성 0.

## 8. 평문 설명 — 사용자가 "어떻게 검증하는지" 인지 (요청 반영)

숫자·수식만 두지 않는다. 두 곳에 자연어 설명:
1. **`docs/HOW-EVAL-WORKS.md`에 "믿을 수 있는 숫자인가" 절 추가**: 신뢰구간("0.75가 아니라 0.52~0.94 — 표본이 작을수록 넓다"), 검정력("3개로 잰 0.8은 사실상 3개 다 맞아야 통과"), McNemar("같은 시험지로 둘을 비교, 바뀐 문제만 센다"), 사후확률("고장이라 확신하는 정도"), judge 투표("한 채점자 말고 셋에게 물어 다수결").
2. **콘솔 "How is this judged?" 패널**: 같은 내용의 4~5줄 요약 + 각 시각 요소 옆 1줄 툴팁("이 막대: 참값이 있을 법한 범위").

## 9. Error Handling
| 상황 | 동작 |
|------|------|
| n=0 / 표본 0 | wilson→(0,1), underpowered=True, 게이트 skip |
| betacf 미수렴(극단값) | 반복 상한 후 경계값 반환(0/1), `ponytail:` 주석에 명시 |
| judge votes 예산 소진 | 얻은 표만으로 다수결, agreement에 반영 |
| compare task_id 불일치 | 교집합만 쌍으로, 나머지 보고 |

## 10. Test Plan
- stats: 레퍼런스 대조 — Wilson(8,10)≈[0.49,0.94], Wilson(3,3), McNemar(b,c) 표, beta_binom_gt(6,10,0.5) 사후, vote 다수결/agreement, betacf 수렴
- 게이트: --strict가 rated=3 차단·rated 충분+ci_lo통과 시 pass; 기본 게이트 불변(회귀)
- judge 투표: N=3 다수결·agreement, budget N배
- compare: McNemar keep/revert/inconclusive 3분기
- 드리프트: p_degraded 값·웹훅 포함
- 콘솔: /evals에 신규 필드; Playwright로 오차막대·패널 렌더
- 회귀: 기존 146 무파손

## 11. Implementation Order
1. [ ] stats.py + 단위검정(레퍼런스 값)
2. [ ] task_eval CI/underpowered/strict + grader 투표 + telemetry 사후 + 테스트
3. [ ] cli --strict/--judge-votes + compare McNemar + 테스트
4. [ ] /evals 필드 + evals.html 시각화 + "How is this judged?" 패널
5. [ ] HOW-EVAL-WORKS.md 평문 절 + Playwright 검증 → gap 분석
