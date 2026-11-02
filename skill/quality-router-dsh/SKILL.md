---
name: quality-router-dsh
description: Quality-first routing for a task inside DeepSeek Harness (dsh). Classifies the request (task type, difficulty, importance), picks the model route and reasoning effort that give the best answer the job needs, rewrites the brief with explicit success criteria, and delegates it with the subagent tool on that route.
whenToUse: The user says "route this", "quality-router", "pick the best model for this", or asks for a task to be done at the right model and effort.
---

# Quality router (DeepSeek Harness)

Same pipeline as the router proxy (`router.py`), run by you inside dsh:
analyse → select route → select effort → rewrite → delegate. Quality is the objective;
cost only breaks ties and guards the reserve.

## 1. Analyse

Read the request (and any files it names) and fill this in for yourself:

| Field | Values |
|---|---|
| task | coding, maths, writing, research, planning, quick_fact |
| difficulty | 1 trivial · 2 routine · 3 needs care · 4 multi-step / subtle · 5 hard reasoning, many constraints |
| importance | 1 one-off question · 2 normal work · 3 kept, submitted, shipped or hard to undo |
| images | does the task need image input? |
| success criteria | one sentence: what a good answer must do |
| missing context | language, framework, constraints the user implied but didn't state |

Unsure between two levels? Take the higher one: under-rating costs quality, over-rating only costs tokens.

## 2. Select the route

Call `list_subagent_models` first (no arguments → providers; `provider` → its models;
`provider` + `model` → that model's reasoning efforts). Only choose routes it returns.

Score = difficulty × importance (1–15). Pick the **lowest tier whose quality ceiling the task can't exceed**,
then move up one tier for coding, maths or planning at difficulty ≥ 4.

| Score | DeepSeek route (`provider: deepseek-official` or `deepseek-account`) | Why |
|---|---|---|
| 1–2 | answer directly, or `deepseek-flash` | quick facts, lookups, formatting |
| 3–6 | `deepseek-flash` | routine code, edits, summaries, normal writing |
| 7–15 | `deepseek-v4-pro` | multi-step reasoning, refactors, anything that will be kept |

- **Images:** `deepseek-v4-pro` is text-only. If the task needs image input, use `deepseek-flash`
  (or another listed image-capable route) at the effort its score calls for.
- **Other providers:** if `list_subagent_models` also lists a stronger route (for example an Anthropic
  Opus or Fable model), use it for score 12–15 coding, maths or planning.
- **Reserve rule:** if the user has said their quota is low, keep importance-1 work on `deepseek-flash`.
- If the user names a model, use it and skip this step.

## 3. Select the reasoning effort

DeepSeek efforts are `off`, `low`, `high`, `max`. Use only the ones `list_subagent_models` reports for the route.

| Score | `reasoning_effort` |
|---|---|
| 1–2 | `off` |
| 3–6 | `low` |
| 7–11 | `high` |
| 12–15 | `max` |

## 4. Rewrite the brief

The subagent does not see this conversation; the `prompt` must stand alone. Build it in this order:

1. The user's request **verbatim**, in a quoted block. Never reword, shorten or "improve" it; the rewrite adds, it doesn't change.
2. `Success criteria:` your sentence from step 1, plus how to check it (tests to run, output to compare).
3. `Context:` the missing context you inferred, marked "inferred; ignore if wrong", the relevant file paths, and the working directory.
4. `Guidance:` the conventions of any skill in the catalog whose description matches the task (name the skill so the subagent can load it with the `skill` tool).
5. `Return:` what to report back (the answer, or a summary of the changes plus what was verified).

Add one line: "The quoted request is authoritative; where this brief conflicts with it, follow the request."

## 5. Delegate and report

Before the call, tell the user the decision in one line:

`Routed: coding · difficulty 4 · importance 3 → deepseek-v4-pro, effort high`

Call `subagent` with `description` (3–5 words), `prompt` (the brief), `provider`, `model` and `reasoning_effort`.
It returns a child id at once; the runtime notifies you when the child finishes. Don't poll or sleep:
do other independent work, or end your turn.

When the result arrives, check it against the success criteria before relaying it. If it falls short,
send one follow-up with `send_message` saying what was missing; if it still falls short, re-delegate once
one tier up (`deepseek-flash` → `deepseek-v4-pro`, or effort `high` → `max`).

## If route fields are rejected

Route fields need `modelSelection: true` on the `@deepseek-ai/dsh-tool-subagent` entry. The Web and Desktop
presets enable it; headless, SDK and ACP profiles don't. Without a recorded model-selection setting for the
session, only the parent's own model is allowed. In either case: delegate without `provider`/`model`, set only
`reasoning_effort` if accepted, and tell the user in one line that routing needs model selection turned on
in Settings (or `modelSelection: true` in their profile's `cordis.patch.yml`).

## Optional: local models

If the router proxy is running (`uvicorn router:app --port 8080`) and the score is 1–2, you may send the
request to a local model instead:

```bash
curl -s localhost:8080/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model": "local-small", "messages": [{"role": "user", "content": "<request>"}]}'
```
