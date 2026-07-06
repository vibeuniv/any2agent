# How self-verification works

*A plain-language guide to what `any2agent eval`, the lessons, and the live
telemetry actually do — and why you can trust the numbers. Also available in
the eval console (`/evals/ui` → "How does this work?").*

---

## The one idea behind everything

**Doing work is hard; checking that work happened is easy.** The eval never
trusts the agent's own words — it grades against independent evidence.

```
your toolset ──▶ generate test tasks ──▶ run them through the REAL agent ──▶ grade with 3 signals
      ▲            (each task ships                                              │
      │             with its own                                                 ▼
      └────────── repair: failures rewrite tool descriptions ◀────────────── failures
                  and schemas, then re-check                                     │
                                                                                 ▼
                                                        lessons: what can't be fixed becomes
                                                        guidance whispered into every chat
```

## The three grading signals

When a test task finishes, the grader collects evidence from three independent
directions — strongest first:

**1. World state — "if it says it created a note, is the note actually there?"**
For write tasks, the grader calls your API *directly* (not through the agent)
and checks the result really exists. Your system's actual state is the answer
key. This is deterministic — no AI judgment involved.

**2. Behavior — "did it call the right tools?"**
Every tool call is recorded. If the task was "list my notes, then open one",
the trace must show the list tool *and* the detail tool actually ran. An agent
that says "done!" without calling anything fails here.

**3. Language — an AI judge (advisory only)**
A separate model checks what the first two can't: "is the answer grounded in
the tool results? does it claim things it didn't do?" It is always the weakest
vote — deterministic signals win.

A task passes only if **every deterministic check passes AND the judge (when
used) agrees.**

## Why the score is honest

The completion rate deliberately excludes things that aren't the agent's fault
— and never counts an unknown as a pass:

| Excluded from the score | Why |
|---|---|
| Write tasks you didn't consent to (`skipped_write`) | never ran |
| Runs cut off by the LLM budget (`skipped_budget`) | money, not skill |
| LLM-provider/infra failures (`infra`) | the plumbing broke, not the agent |
| Tasks with nothing measurable (`ungraded`) | **excluded, never silently passed** |

And the gate fails outright if a write task left un-cleaned test data behind
(`residue`) — a high score never excuses a mess.

## What happens after a failure

1. Every failure is classified into one of five causes — wrong tool picked,
   invalid arguments, the API call failed, the claimed result wasn't found,
   or the answer missed the point — and shown as one "what to fix" line.
2. Fixable causes can be repaired automatically (`eval --fix` rewrites the
   confusing tool description with the failure as context, or re-infers a
   rejected parameter schema).
3. What can't be auto-fixed becomes a **lesson**: a one-line correction
   injected into every future conversation ("for requests like X, use tool Y").
   Lessons are hints only — they can never loosen the write-confirmation or
   permission rules — and each one deletes itself the moment its task passes.

## Test runs vs. real life

The eval is a **build-time** check. Once real conversations flow, **telemetry**
takes over: every executed tool call is logged (name, outcome, speed — never
your arguments, responses, or user identity). If a tool starts failing in most
of its recent live calls, the console flags it as a *suspect* — the usual cause
is that your API changed underneath the agent — and tells you to re-run the
eval. A recovered tool clears itself automatically.

## Where to look

| Question | Where |
|---|---|
| Is my agent working right now? | chat header badge (`✅ 0.88 · 5 runs`, `⚠` = live trouble) |
| What failed and what do I do? | `/evals/ui` → "What went wrong" |
| Am I getting better over time? | `/evals/ui` chart, or `any2agent eval --history` |
| Full machine-readable report | `any2agent eval --json report.json` |
| The tasks themselves | `yourapp.evals.json` — edit them; your edits always win |

Run it any time: `any2agent eval --project yourapp` — exit code 0 means the
gate passed (CI-friendly).
