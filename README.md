# high-quality-plugin

> [!WARNING]
> **Development has stopped. This project will not get any more updates.**
> The code still works as described below, but it is no longer maintained, issues won't be answered,
> and the planned "command mode" for DeepSeek Harness will not be built.
> [Why development stopped](#why-development-stopped)

## Why development stopped

The idea was to send each message to whichever model suits it best. Research into what switching models
actually costs showed that the gain is small and the cost is real, so the plugin isn't worth continuing.

**Switching models throws away the prompt cache.** Every message makes the model re-read the whole
conversation. Servers cache the part they've already processed, so later turns only pay full price for the
new part. That cache belongs to one model; a different model can't use it. So every switch means:

- **Slower replies.** The first reply after a switch takes longer to start, because the new model processes
  the entire history from scratch. The longer the chat, the longer the delay.
- **More tokens.** Cached tokens cost about 10% of the normal price. After a switch, the whole history is
  billed at full price, plus a little extra to build the new cache: roughly one full re-read of the
  conversation per switch. Switching often in a long chat uses up limits much faster. Switching back
  doesn't help for long either, because caches expire after about 5 minutes (up to an hour on some setups).
- **Slightly worse answers, not better.** The new model sees earlier replies only as text, without the
  reasoning behind them. It has to pick up another model's style and approach, so tone and formatting can
  shift, and in long chats it may give less weight to early details.

**The quality gain doesn't make up for it.** When the available models are roughly equally capable, picking
a different one per message improves answers only a little. A router that switches on every message pays
the cost above again and again for that small gain.

**The better habit needs no plugin:** pick a model at the start and stay on it. If a long task really needs
a different model, start a new chat with a short summary instead of switching in the middle.

---

Quality-first model routing: work out what a task needs, pick the model and effort that give the best
answer for it, add the guidance the model needs, then send it. It comes in three forms:

| Part | What it is | Where it runs |
|---|---|---|
| [Claude Code skill](#claude-code-skill) | `skill/quality-router` | Claude Code |
| [DeepSeek Harness skill](#deepseek-harness-skill) | `dsh/skills/quality-router-dsh` (plugin `dsh-quality-router`) | DeepSeek Harness (`dsh`) |
| [Router proxy](#router-proxy) | `router.py` | Any OpenAI-compatible client (e.g. Cherry Studio) |

The skills don't need the proxy, and don't need Python. Install only the parts you want.

## Claude Code skill

Downloads the skill into your personal Claude Code skills folder (`~/.claude/skills`), creating it if needed.

**macOS / Linux / Git Bash**

```bash
mkdir -p ~/.claude/skills/quality-router && curl -fsSL https://raw.githubusercontent.com/William20100221/high-quality-plugin/main/skill/quality-router/SKILL.md -o ~/.claude/skills/quality-router/SKILL.md
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills\quality-router" | Out-Null; Invoke-WebRequest https://raw.githubusercontent.com/William20100221/high-quality-plugin/main/skill/quality-router/SKILL.md -OutFile "$HOME\.claude\skills\quality-router\SKILL.md"
```

Start a new Claude Code session, then say `route this: <your task>` or type `/quality-router <your task>`.
To install it for one project only, put the folder in `<project>/.claude/skills/` instead.

## DeepSeek Harness skill

This repo is also a [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) plugin
(`dsh-quality-router`). Installed as a plugin, it appears on the **Plugins** page and its skill is added to
every session. It contains no code: only the skill and a config file that tells DSH where the skill is.

**Install as a plugin (recommended).** Add the GitHub repo `William20100221/high-quality-plugin` the same
way you add any GitHub plugin, or from a terminal (replace `desktop` with your profile: `desktop` for the
Desktop app, `web` for `dsh web`):

```bash
npx @deepseek-ai/dsh plugin --profile desktop add github:William20100221/high-quality-plugin
```

Restart DSH. `dsh-quality-router` appears on the Plugins page and `quality-router-dsh` in the skill list.

**Or copy just the skill** into your personal skills folder (`~/.dsh/skills`, or `$DSH_HOME/skills` if you've
set `DSH_HOME`). It works the same but doesn't appear on the Plugins page. Don't do both.

macOS / Linux / Git Bash:

```bash
mkdir -p ~/.dsh/skills/quality-router-dsh && curl -fsSL https://raw.githubusercontent.com/William20100221/high-quality-plugin/main/dsh/skills/quality-router-dsh/SKILL.md -o ~/.dsh/skills/quality-router-dsh/SKILL.md
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.dsh\skills\quality-router-dsh" | Out-Null; Invoke-WebRequest https://raw.githubusercontent.com/William20100221/high-quality-plugin/main/dsh/skills/quality-router-dsh/SKILL.md -OutFile "$HOME\.dsh\skills\quality-router-dsh\SKILL.md"
```

The skill picks a model per task (`deepseek-flash` or `deepseek-v4-pro`, or a stronger route if your
session lists one) and sets `reasoning_effort`. That needs model selection turned on for subagents: it is on
by default in the Web and Desktop apps, but not in headless, SDK or ACP profiles. Without it the skill still
works, on your current model only, and tells you so.

## Router proxy

A local proxy between your chat client and your models (LM Studio, Claude). Every request is analysed,
routed to the best model the budget allows, given an effort level, and has skill guidance added before it
is sent. Needs **Python 3.12 or newer**.

**1. Get the code and install dependencies**

```bash
git clone https://github.com/William20100221/high-quality-plugin.git
```

```bash
cd high-quality-plugin
```

```bash
python -m pip install fastapi uvicorn httpx pyyaml anthropic
```

No git? Download the ZIP from the green **Code** button on GitHub and unzip it instead.
On macOS / Linux, use `python3` if `python` isn't found.

**2. Check it works** (uses a fake upstream, no models or API keys needed; prints `ok`)

```bash
python test_router.py
```

**3. Configure your models** in `registry.yaml`:

- Set each local model's `url` to your LM Studio server (default `http://localhost:1234/v1`) and `model`
  to the model id LM Studio shows.
- For Claude, set the `ANTHROPIC_API_KEY` environment variable (or run `ant auth login`).
- Remove models you don't have; `analyser` must name one that's left (a small, fast local model is best).

**4. Run it**

```bash
python -m uvicorn router:app --port 8080
```

Point your client at `http://localhost:8080/v1`. Use model `router` to route automatically, or a registry
name (e.g. `claude-opus`) to force one model.

| What | Where |
|---|---|
| Models, quality per task type, quotas, prompt style | `registry.yaml` |
| Skills injected into prompts (matched by `tasks` / `keywords` in the header) | `skills/*.md` |
| Original prompt next to the guidance added, plus model, effort, tokens, errors | `http://localhost:8080/log` |
| The decision for each response | headers `x-router-model`, `x-router-task`, `x-router-effort` |

Limits: Claude Code can't use the proxy (it speaks Anthropic's `/v1/messages`, not the OpenAI format).
Images and tool calls aren't passed to Claude yet, and local models stream text only.
