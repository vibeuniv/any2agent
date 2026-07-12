# eval-stats Planning Document

> **Summary**: 검증 파이프라인에 통계적 추론(신뢰구간·검정력·쌍체검정·사후확률)을 넣고, 그 불확실성을 콘솔에서 눈으로 보고 판단·개선하게 한다
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-12
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 검증이 *무엇을* 재는지는 정직하지만 *얼마나 확신하는지*를 수학적으로 다루지 않는다. rate 0.8을 정확값처럼 게이트하는데, rated=3~4가 흔해 "0.8 게이트"가 몰래 100% 게이트가 되고(진짜 0.90 에이전트가 27~34% 확률로 fail), compare 판정은 유의성을 낼 수 없는 표본에서 내려진다. 사용자는 이 불확실성을 화면에서 볼 수 없다. |
| **Solution** | (1) 순수 stdlib 통계 엔진(`evals/stats.py`): Wilson 점수 구간, 최소표본/검정력, McNemar 정확검정(쌍체 compare), 베타-이항 사후확률(드리프트), judge k-투표. (2) 기본 rate 게이트는 유지하되 `--strict`로 "CI 하한 ≥ 임계 + 최소 rated" 옵인. (3) 기존 `evals.html`을 확장해 rate에 **오차막대**, 표본 부족 시 **검정력 경고**, compare **McNemar 판정**, 드리프트 **사후확률**, judge **투표 분포**를 시각화. |
| **Function/UX Effect** | 콘솔에서 "0.75 ± 0.28 (표본 부족)" 처럼 **불확실성이 보이고**, 게이트 통과/실패가 운인지 실력인지 눈으로 판단하고 표본을 늘릴지 결정할 수 있다. `--strict`는 CI에서 통계적으로 옳은 차단을 준다. |
| **Core Value** | 검증의 정직성을 수치 신뢰성까지 끌어올리고, 그 신뢰성을 사람이 보고 개선하는 루프를 닫는다 — 새 의존성 0. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 점추정을 정확값처럼 게이트/시각화 — 소표본에서 판정이 노이즈 지배 |
| **WHO** | eval을 CI 게이트로 쓰는 개발자, 콘솔로 품질을 판단하는 이해관계자 |
| **RISK** | 게이트 의미 변경이 기존 exit code를 깸 → **기본은 하위호환, 통계 게이트는 --strict 옵인** (사용자 확정) |
| **SUCCESS** | stats.py 단위검정 통과, --strict가 소표본을 underpowered로 정직 차단, 콘솔에 CI·검정력·McNemar·사후확률·judge투표 표시, 신규 의존성 0, 기존 146 테스트 무파손 |
| **SCOPE** | 통계 엔진 + opt-in 게이트 + 기존 콘솔 확장. 전용 통계 탭·다중 실행 반복 수집은 후속 |

---

## 1. Overview

### 1.1 Purpose
검증 결과에 불확실성 정량화를 부여하고(엔진), 그것을 게이트(선택)와 화면(시각화)에 노출해
사람이 "이 숫자를 믿을 수 있나 / 표본을 늘려야 하나 / 새 toolset이 정말 나은가"를 판단·개선하게 한다.

### 1.2 Background
- 검토(2026-07-12)에서 확인된 통계적 공백: 신뢰구간 없음, judge 단일 draw, compare가 소표본 점추정 비교, 드리프트 임계 오경보율 미분석, 소표본 실효임계 불연속.
- 사용자 결정: 게이트는 opt-in(`--strict`), 시각화는 기존 콘솔 확장.

### 1.3 Related Documents
- 통계 검토 근거(대화 기록) / [eval-console 아카이브](../../archive/2026-07/eval-console/)
- Design: [eval-stats.design.md](../../02-design/features/eval-stats.design.md)

---

## 2. Scope

### 2.1 In Scope
- [ ] **`evals/stats.py`** (순수 stdlib): `wilson(k,n,conf=0.95)`, `min_n_for(halfwidth,p)`/검정력 경고, `mcnemar_exact(b,c)`, `beta_binom_suspect(errors,n,prior)` 사후확률, `vote(passes)` k-투표 집계
- [ ] **task_eval에 CI·표본 필드 추가**: `rate_ci`(lo,hi), `rated`, `underpowered` 플래그 — 리포트/`/evals`에 노출 (게이트 로직은 기본 불변)
- [ ] **`eval --strict`**: 게이트를 `ci_lo ≥ threshold AND rated ≥ MIN_RATED`로. 기본(비-strict)은 기존 `rate ≥ threshold` 유지
- [ ] **judge k-투표**(opt-in `--judge-votes N`, 기본 1=현행): N표 다수결 + 불일치율(`judge_agreement`) 리포트
- [ ] **compare McNemar**: 쌍체 pass/fail로 `mcnemar_exact` → verdict에 discordant 쌍수·p값·"표본부족" 표기
- [ ] **드리프트 사후확률**: `telemetry.summary`의 suspect에 `p_degraded`(베타-이항 사후) 추가, 콘솔이 카운트 대신/과 함께 표시
- [ ] **콘솔 확장(evals.html)**: rate 오차막대(Wilson), underpowered 배지, compare McNemar 카드, 드리프트 사후확률 바, judge 투표 분포 — 단일 파일 유지

### 2.2 Out of Scope (후속)
- 전용 통계 탭/분포 그래프 (사용자: 기존 콘솔 확장 선택)
- 태스크 다회 반복 실행으로 실행-분산 추정 (지금은 1회 + 이항 CI)
- 베이지안 계층모델·태스크 난이도 보정

---

## 3. Requirements

### 3.1 Functional Requirements
| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `stats.py`: Wilson 구간·McNemar 정확검정·베타이항 사후·k-투표, 전부 stdlib(math)만 | High |
| FR-02 | task_eval 리포트에 `rate_ci=[lo,hi]`, `underpowered`(rated<MIN_RATED 또는 CI폭>0.3) 추가 — 기본 게이트 불변 | High |
| FR-03 | `eval --strict`: `ci_lo≥threshold AND rated≥MIN_RATED`; 미달 시 exit 1 + 사유("underpowered: need N more tasks") | High |
| FR-04 | `--judge-votes N`(기본 1): N표 다수결, `judge_agreement` 리포트; budget에 N배 반영 | Medium |
| FR-05 | compare: McNemar 정확검정, verdict = keep/revert/**inconclusive**(불일치 쌍<임계) + p값 표기 | High |
| FR-06 | 드리프트 suspect에 `p_degraded`(사후확률) 추가; 웹훅/콘솔이 표기 | Medium |
| FR-07 | evals.html: CI 오차막대·underpowered 배지·McNemar 카드·사후확률 바·judge 투표 — 단일 파일, 신규 의존성 0 | High |

### 3.2 Non-Functional
| Category | Criteria | Method |
|----------|----------|--------|
| 의존성 | 신규 서드파티 0 (numpy/scipy 금지, math만) | pyproject 확인 |
| 하위호환 | 기본 게이트·exit code·리포트 키 불변, 필드는 추가만 | 기존 146 테스트 무파손 |
| 정확성 | Wilson/McNemar/사후 값이 알려진 레퍼런스와 일치 | 문헌 값 대조 단위검정 |
| 무LLM | 통계 계층은 키 없이 전부 동작(judge 투표만 키 필요) | notes-api 확인 |

---

## 4. Success Criteria
- [ ] stats.py가 레퍼런스 값(예: Wilson(8,10)=[0.49,0.94], McNemar 표) 재현
- [ ] `--strict`가 rated=3에서 "underpowered"로 정직 차단, rated 충분+CI하한 통과 시 pass
- [ ] `eval --compare`가 불일치 쌍 부족 시 "inconclusive" 반환
- [ ] 콘솔에서 rate 오차막대·검정력 경고·McNemar·사후확률·judge 투표가 브라우저로 확인됨(Playwright)
- [ ] Gap 분석 ≥ 90%, 신규 의존성 0

---

## 5. Risks and Mitigation
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| stdlib로 통계 함수 구현 오류 | High | Medium | 문헌 레퍼런스 값 대조 단위검정(FR-01) + `ponytail:` 근사 한계 주석 |
| --strict가 유용한 소표본 실행을 과차단 | Medium | Medium | 기본 아님(옵인), 경고에 "필요 추가 태스크 수" 명시해 개선경로 제공 |
| judge N배 호출로 비용↑ | Medium | Low | 기본 1 유지, budget에 반영, 콘솔에 투표 비용 표기 |
| 콘솔 과밀(단일 파일에 요소 증가) | Low | Medium | 통계 요소는 기존 카드에 인라인 부착(새 섹션 최소) |

---

## 6. Impact Analysis
| Resource | Type | Change |
|----------|------|--------|
| `evals/stats.py` | 신규 | 순수 함수 모듈 |
| `verifier.task_eval` | API | 리포트에 CI/underpowered 필드 추가(기존 키 불변), `strict`/`judge_votes` 인자 |
| `evals/grader._judge` | 내부 | k-투표 래퍼(기본 1) |
| `evals/telemetry.summary` | API | suspect에 p_degraded 추가 |
| `cli.py` (eval) | CLI | `--strict`, `--judge-votes`; compare verdict McNemar화 |
| `server/app.py /evals` | API | CI/사후확률 필드 통과 |
| `server/web/evals.html` | UI | 시각화 확장 |

소비자 검증: task_eval 리포트 키는 **추가만**(기존 rate/passed/threshold 유지) → history/console/compare 무파손. connect `_eval_gate`는 기본(비-strict) 경로라 불변.

---

## 7. Architecture Considerations
Python CLI + 단일 파일 웹. 통계는 `evals/stats.py` 순수 함수(부작용·의존성 0)로 격리해
runner/grader/verifier/telemetry가 소비. 웹은 기존 바닐라 JS에 SVG 오차막대(라이브러리 없이).
정본은 stats.py — 게이트·콘솔·compare가 같은 함수를 공유.

---

## 8. Next Steps
1. [ ] Design (`/pdca design eval-stats`) — stats.py 함수 시그니처·수식, 게이트/콘솔 계약, 검정 계획
2. [ ] 구현 → `/pdca analyze eval-stats`

---

## Version History
| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-12 | Initial draft (opt-in --strict + console-extend, 사용자 확정) | jhchoi |
