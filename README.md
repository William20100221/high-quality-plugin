# router

Quality-first OpenAI-compatible proxy: analyse task → pick model → pick effort → add guidance → forward.

```
pip install fastapi uvicorn httpx pyyaml anthropic
uvicorn router:app --port 8080
python test_router.py      # self-check against a fake upstream
```

Point Cherry Studio at `http://localhost:8080/v1`, model `router` (auto) or a registry name (forced).

- `registry.yaml` — models, quality per task type, quotas, prompt style, which model is the analyser.
- `skills/*.md` — frontmatter `tasks` / `keywords` decide when a skill is injected.
- `GET /log` — original prompt next to the guidance that was added, plus model, effort, tokens, errors.
- Response headers `x-router-model`, `x-router-task`, `x-router-effort` show each decision.

## Skill version

`skill/quality-router/SKILL.md` runs the same pipeline inside Claude Code: it classifies the task, picks
the model (haiku / sonnet / opus / fable) and effort by quality, writes a brief that keeps your request
verbatim plus success criteria, and dispatches it to a subagent. Install:

```
cp -r skill/quality-router ~/.claude/skills/
```

Then say "route this: …" or `/quality-router …` in Claude Code.

### DeepSeek Harness version

`skill/quality-router-dsh/SKILL.md` does the same in [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness):
it checks `list_subagent_models`, routes between `deepseek-flash` and `deepseek-v4-pro` (or a stronger listed
route), sets `reasoning_effort` (`off` / `low` / `high` / `max`) and delegates with the `subagent` tool.
Needs model selection on (default in the Web and Desktop apps). Install:

```
cp -r skill/quality-router-dsh ~/.dsh/skills/
```
