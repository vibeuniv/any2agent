# self-verification — Gap Analysis

> **Feature**: self-verification (task-based eval harness)
> **Design Doc**: [self-verification.design.md](self-verification.design.md)
> **Analyzed**: 2026-07-05 (gap-detector agent)
> **Tests at analysis time**: 23/23 passed (live-LLM cycle not executed — no provider key in env)

## Match Rate: 92.0% ✅

`(64 full × 1.0) + (10 partial × 0.5) + (1 miss × 0.0) = 69.0 / 75 designed items = 0.920`

- ✅ Full: 64 · ⚠️ Partial: 10 · ❌ Miss: 1
- FR coverage: **8/8 (100%)** · Security §7: **5/5** · Conventions: **100%**

**Verdict**: Design and implementation match well. Every FR and module contract is
present. Gaps are (a) signature/field/reporting deviations that are functionally
equal or better, (b) one genuine miss (`--judge-model`), (c) one budget-semantics
deviation with real cost impact.

## Top gaps (act on these)

1. **Budget semantics (Medium)** — `budget.py`/`runner.py:31-33` spend **1 unit per
   task-run**, not per inner LLM call. Design §5.2's "40회 = 생성 + 러너 태스크당
   ≤MAX_STEPS + judge" implies inner `run_chat` calls count. Actual cap ≈ 40
   task-runs × up to 8 inner calls ≈ 320+ LLM calls (`--n` default 8 mitigates).
   Decide: sync design to the per-run cap (recommended) or count inner calls.
2. **`--judge-model` missing (Miss)** — design §5.1/§6 list it; `cli.py` has no such
   flag and `grader._judge` always uses the agent's `model_id`. Add it or drop it
   from the design.
3. **`skipped_budget` mislabeled (Low-Med)** — `runner.py:32` sets
   `trace.error="skipped_budget"`, which `verifier.py` folds into `infra_errors`.
   Design §8 wants a distinct bucket so CI can tell "out of budget" from "infra broke."

## Deviations (reverse direction) — severity

| # | Deviation | Evidence | Judgment | Sev |
|:-:|-----------|----------|----------|:---:|
| 1 | `EvalTrace.rounds:int` replaces design's `llm_calls:int` | `model.py:54`, `runner.py:57-61` | Not a literal call count — counts tool→output cycles (best-effort, 0=unknown). Same reporting role. | Low |
| 2 | `EvalTrace.write_blocked:str` (tool name) vs design `bool` | `model.py:56`, `runner.py:54-56`, `grader.py:46-48` | Superior — carries which tool; truthiness preserves bool semantics. | Low (improvement) |
| 3 | Budget per task-run, not per inner LLM call | `runner.py:12-14,31-33`, `budget.py` | Real cost-cap weakening (see gap 1). | **Medium** |
| 4 | `skipped_budget` folded into `infra_errors` | `runner.py:32`, `verifier.py:166` | Reported, not silent, but mislabeled. | Low-Med |
| 5 | metrics `steps`→`rounds`; `wrong_tool`/`errors` are counts, not the `%` §6 shows | `grader.py:28-39`, `verifier.py:177-181`, `cli.py:153-156` | Same info, cosmetic framing. | Low |
| 6 | `load_or_generate(toolset, evals_path,…) -> (valid, invalid)` vs design `(…,cfg,…) -> list` | `tasks.py:186-196` | Enhancement — invalid list is required for §4.1 "no silent drop." | Low (improvement) |
| 7 | `grade(task, trace, toolset, adapter,…)` adds `toolset` | `grader.py:22-23` | Necessary for `state`-check tool lookup. | Low (necessary) |
| 8 | Judge fires only when no det checks **AND** no expected_tools; §4.3 literal: "checks 비면" | `grader.py:72` | Treats expected_tools as deterministic signal — arguably more correct. | Low |
| 9 | `connect --eval` gate runs after loop unconditionally, not only "루프 통과 후" | `connect.py:320-326` | Minor; runs "after the loop." | Low |

## Partial-match items (the 10 counted at 0.5)

EvalTrace fields (dev 1,2) · EvalResult metrics keys (dev 5) · `load_or_generate`
signature (dev 6) · `grade` signature (dev 7) · judge-trigger condition (dev 8) ·
`task_eval` metric framing (dev 5) · budget accounting (dev 3) · budget-exhaustion
label (dev 4) · integration test scope · automated happy-path E2E.

## FR coverage — 8/8 ✅

| FR | Evidence |
|----|----------|
| FR-01 태스크 자동 생성 | `tasks.generate_llm` + `generate_fallback` |
| FR-02 실루프 실행·트랜스크립트 | `runner.run_task` |
| FR-03 이중 채점 | `grader.grade` (결정적 체크 + advisory judge) |
| FR-04 게이트 | `verifier.task_eval` + `THRESHOLDS["task_eval_rate"]=0.8` |
| FR-05 CLI | `cli.cmd_eval` (`--json`, exit 0/1/2) |
| FR-06 repair 피드백 | `connect._eval_repair` (enrich context + synth_params 4xx) |
| FR-07 write 안전장치 | `--live-write` + auto_confirm + `run_cleanup` + 마커 검증 |
| FR-08 connect 통합 | `connect --eval` → `_eval_gate` (2-attempt) |

FR-05/FR-08 are code-complete but the live-LLM end-to-end run was **not** executed
in this environment (no provider key).

## Test plan (§9) — 7/9 ✅, 2 ⚠️

- ✅ grader all check types · tasks fallback/validate/curation · runner confirm 3
  branches · regression `run_all(eval_tasks=None)` · pyproject pytest+dev extra ·
  error case (write_blocked/wrong_tool) · edge case (ungraded)
- ⚠️ Integration full cycle — stdlib `HTTPServer` (not uvicorn); covers transport +
  `state` check + cleanup only; eval→rate cycle deferred to CI-with-keys
- ⚠️ Happy key-case (read 2-step → success) — deterministic pieces tested;
  LLM-driven E2E not automated. Both consistent with the no-key environment.

## Post-analysis fixes (applied 2026-07-05, same session)

| Gap | Action | Evidence |
|-----|--------|----------|
| 1. Budget semantics | Design §5.2 doc-synced to the per-run cap (1 unit = eval-initiated interaction; inner run_chat bounded by MAX_STEPS) | design.md §5.2 |
| 2. `--judge-model` | Implemented end-to-end: CLI flag → `task_eval(judge_model=)` → `grade(judge_model=)` → `_judge` | `cli.py`, `verifier.py`, `grader.py` |
| 3. `skipped_budget` | Split into its own report field/bucket, no longer folded into `infra_errors`; CLI prints it; regression test added | `verifier.py`, `cli.py`, `test_verifier_task_eval.py` |
| Doc-sync (dev 1,2,5,6,8) | Design §3 (rounds / write_blocked:str), §4.1/§4.3 signatures, judge-trigger wording, §6 sample output synced to code | design.md |

**Post-fix Match Rate: ~99%** — remaining ⚠️ are environment-bound only (live-LLM
integration slice needs CI with provider keys). Tests: 24/24 passed.

## Recommended next actions

- **Code**: add/decide `--judge-model`; split `skipped_budget` out of
  `infra_errors`; resolve budget granularity (recommend documenting the per-run cap
  in design §5.2).
- **Doc-sync (code is truth)**: update design §3 (rounds / write_blocked:str /
  ungraded / metrics keys), §4.1 & §4.3 signatures, §6 sample output (counts vs %).
- **Test**: add the automated live-LLM `any2agent eval` slice on
  `examples/notes-api` under CI-with-keys.
- Score ≥ 90% → `/pdca iterate` not required; proceed to
  `/pdca report self-verification` after the three code fixes (or doc-sync first).
