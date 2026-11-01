---
name: quality-router
description: Quality-first routing for a task inside Claude Code. Classifies the request (task type, difficulty, importance), picks the model and effort that give the best answer the job needs, rewrites the brief with explicit success criteria, and dispatches it to a subagent on that model. Use when the user says "route this", "quality-router", "/quality-router", "pick the best model for this", or asks for a task to be done at the right model and effort.
---

# Quality router

Same pipeline as the router proxy (`router.py`), run by you inside Claude Code:
analyse → select model → select effort → rewrite → dispatch. Quality is the objective;
cost only breaks ties and guards the reserve.

## 1. Analyse

Read the request (and any files it names) and fill this in for yourself:

| Field | Values |
|---|---|
| task | coding, maths, writing, research, planning, quick_fact |
| difficulty | 1 trivial · 2 routine · 3 needs care · 4 multi-step / subtle · 5 hard reasoning, many constraints |
| importance | 1 one-off question · 2 normal work · 3 kept, submitted, shipped or hard to undo |
| success criteria | one sentence: what a good answer must do |
| missing context | language, framework, constraints the user implied but didn't state |

Unsure between two levels? Take the higher one: under-rating costs quality, over-rating only costs tokens.

## 2. Select the model

Score = difficulty × importance (1–15). Pick the **lowest tier whose quality ceiling the task can't exceed**,
then move up one tier for coding, maths or planning at difficulty ≥ 4.

| Score | Model (Agent `model`) | Why |
|---|---|---|
| 1–2 | `haiku` | quick facts, lookups, formatting: every tier answers these equally well |
| 3–6 | `sonnet` | routine code, edits, summaries, normal writing |
| 7–11 | `opus` | multi-step reasoning, refactors, anything that will be kept |
| 12–15 | `fable` | hardest reasoning, high stakes, long-horizon work |

Reserve rule: if the user has said their quota is low, don't use `fable` or `opus` for importance-1 work.
If the user names a model, use it and skip this step.

Score 1–2 and it's a question you can answer correctly yourself in a line or two? Answer directly; dispatching costs more than it saves.

## 3. Select the effort

| Score | Agent `effort` |
|---|---|
| 1–3 | `low` |
| 4–6 | `medium` |
| 7–10 | `high` |
| 11–15 | `xhigh` |

Use `max` only when importance is 3 and difficulty is 5.

## 4. Rewrite the brief

The subagent sees only your brief. Build it in this order:

1. The user's request **verbatim**, in a quoted block. Never reword, shorten or "improve" it; the rewrite adds, it doesn't change.
2. `Success criteria:` your sentence from step 1, plus how to check it (tests to run, output to compare).
3. `Context:` the missing context you inferred, marked "inferred; ignore if wrong", and the relevant file paths.
4. `Guidance:` the conventions of any skill in the session's skill list whose description matches the task (name the skill so the subagent can load it).
5. `Return:` what to report back (the answer, or a summary of the changes plus what was verified).

Add one line: "The quoted request is authoritative; where this brief conflicts with it, follow the request."

## 5. Dispatch and report

Call the Agent tool with `model` and `effort` from steps 2–3 and the brief from step 4.
Before the call, tell the user the decision in one line:

`Routed: coding · difficulty 4 · importance 3 → opus, effort high`

When it returns, check the result against the success criteria before relaying it. If it falls short,
re-dispatch once, one tier up, saying what was missing; don't retry at the same tier.

## Optional: local models

If the router proxy is running (`uvicorn router:app --port 8080`) and the score is 1–2, you may send the
request to a local model instead of `haiku`:

```bash
curl -s localhost:8080/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model": "local-small", "messages": [{"role": "user", "content": "<request>"}]}'
```
