"""The router: OpenAI-compatible proxy. Each request is analysed, routed to the best
model the budget allows, given an effort level, and has guidance added before it is sent.
Run: uvicorn router:app --port 8080   then point your client at http://localhost:8080/v1
"""
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import anthropic
import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).parent
REG = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
MODELS = REG["models"]
TASKS = ["coding", "maths", "writing", "research", "planning", "quick_fact"]
EFFORTS = ["low", "medium", "high", "xhigh"]
LOCAL_MAX_TOKENS = {"low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384}

db = sqlite3.connect(os.environ.get("ROUTER_DB", ROOT / "router.db"), check_same_thread=False)
db.execute("""create table if not exists requests(id text, ts real, model text, effort text, task text,
  original text, added_system text, input_tokens int default 0, output_tokens int default 0, error text)""")

http = httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3, read=600))
_claude = None


def claude():
    global _claude
    _claude = _claude or anthropic.AsyncAnthropic()
    return _claude


def load_skills():
    skills = []
    for p in sorted((ROOT / "skills").glob("*.md")):
        _, head, body = p.read_text(encoding="utf-8").split("---", 2)
        skills.append({**yaml.safe_load(head), "body": body.strip()})
    return skills


SKILLS = load_skills()


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


# ---------- 1. task analyser ----------

ANALYSER_PROMPT = f"""Classify the user's request. Reply with one JSON object only:
{{"task": one of {TASKS},
 "difficulty": 1-5,
 "importance": 1-3 (1 = one-off question, 3 = work that will be kept, submitted or shipped),
 "success_criteria": "one sentence: what a good answer must do",
 "inferred_context": "language, framework or constraints implied but not stated, or empty"}}"""

HINTS = {
    "coding": r"```|\b(code|function|bug|error|traceback|python|javascript|api|class|refactor|compile)\b",
    "maths": r"\b(prove|integral|derivative|equation|solve for|probability|matrix)\b",
    "planning": r"\b(plan|schedule|roadmap|timeline|milestones)\b",
    "research": r"\b(compare|sources|research|literature|evidence)\b",
}
_cache = {}


def clean(t):
    def clamp(v, lo, hi, default):
        return max(lo, min(hi, int(v))) if isinstance(v, (int, float)) else default

    return {
        "task": t.get("task") if t.get("task") in TASKS else "writing",
        "difficulty": clamp(t.get("difficulty"), 1, 5, 3),
        "importance": clamp(t.get("importance"), 1, 3, 2),
        "success_criteria": str(t.get("success_criteria") or ""),
        "inferred_context": str(t.get("inferred_context") or ""),
    }


def heuristic(prompt):
    task = next((t for t, rx in HINTS.items() if re.search(rx, prompt, re.I)),
                "quick_fact" if len(prompt) < 120 else "writing")
    return clean({"task": task, "difficulty": 2 if len(prompt) < 300 else 3})


async def analyse(prompt):
    if prompt in _cache:
        return _cache[prompt]
    m = MODELS[REG["analyser"]]
    try:
        r = await http.post(m["url"] + "/chat/completions", timeout=httpx.Timeout(20, connect=2), json={
            "model": m["model"], "temperature": 0, "max_tokens": 300,
            "messages": [{"role": "system", "content": ANALYSER_PROMPT},
                         {"role": "user", "content": prompt[:4000]}]})
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]
        task = clean(json.loads(re.search(r"\{.*\}", reply, re.S).group()))
    except Exception:  # analyser offline or gave junk: rules still route sensibly
        task = heuristic(prompt)
    if len(_cache) > 1000:  # ponytail: wipe-all cache, use an LRU if hit rate matters
        _cache.clear()
    _cache[prompt] = task
    return task


# ---------- 2. selector ----------

def quota_left(name):
    quota = MODELS[name].get("quota_tokens")
    if not quota:
        return 1.0
    month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    used = db.execute("select coalesce(sum(input_tokens + output_tokens), 0) from requests "
                      "where model = ? and ts >= ?", (name, month)).fetchone()[0]
    return 1 - used / quota


def rank(task, ctx_tokens):
    """Best quality first; budget is a constraint, not the objective."""
    fits = []
    for name, m in MODELS.items():
        q = m.get("quality", {}).get(task["task"], 0)
        left = quota_left(name)
        if q <= 0 or m.get("context", 0) < ctx_tokens or left <= 0:
            continue
        if left < REG["reserve"] and task["importance"] < 3:
            continue
        fits.append((q, name))
    return [name for _, name in sorted(fits, key=lambda f: -f[0])]


# ---------- 3. effort chooser ----------

def choose_effort(task, name):
    score = task["difficulty"] * task["importance"]  # 1..15
    level = 0 if score <= 3 else 1 if score <= 6 else 2 if score <= 10 else 3
    if quota_left(name) < REG["reserve"]:
        level = min(level, 1)
    return EFFORTS[level]


# ---------- 4. prompt rewriter (adds guidance; never edits the user's messages) ----------

def rewrite(task, prompt, name):
    p = prompt.lower()
    skills = [s for s in SKILLS
              if task["task"] in s.get("tasks", []) or any(k in p for k in s.get("keywords", []))][:3]
    parts = [("skill", s["name"], s["body"]) for s in skills]
    if task["success_criteria"]:
        parts.append(("success_criteria", "", task["success_criteria"]))
    if task["inferred_context"]:
        parts.append(("inferred_context", "", task["inferred_context"] + " (inferred; ignore if wrong)"))
    if not parts:
        return ""
    head = "Guidance added by a router. The user's messages are authoritative; where they conflict with this, follow the user."
    if MODELS[name].get("prompt_style") == "xml":
        body = [f'<{tag}{f" name=\"{title}\"" if title else ""}>\n{text}\n</{tag}>' for tag, title, text in parts]
    else:
        body = [f"## {title or tag.replace('_', ' ')}\n{text}" for tag, title, text in parts]
    return "\n\n".join([head, *body])


# ---------- backends: async generators of text chunks ----------

async def call_anthropic(m, body, added, effort, usage):
    system = "\n\n".join([text_of(x.get("content")) for x in body["messages"]
                          if x.get("role") in ("system", "developer")] + ([added] if added else []))
    # ponytail: text only; images and tool calls aren't translated to Messages format yet
    msgs = [{"role": x["role"], "content": text_of(x.get("content"))} for x in body["messages"]
            if x.get("role") in ("user", "assistant") and text_of(x.get("content"))]
    kw = dict(model=m["model"], max_tokens=body.get("max_tokens") or 32000, messages=msgs,
              thinking={"type": "adaptive"}, output_config={"effort": effort})
    if system:
        kw["system"] = system
    if m.get("refusal_fallback"):
        kw |= dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    async with claude().beta.messages.stream(**kw) as s:
        async for t in s.text_stream:
            yield t
        final = await s.get_final_message()
    usage.update(input=final.usage.input_tokens, output=final.usage.output_tokens,
                 finish={"max_tokens": "length", "refusal": "content_filter"}.get(final.stop_reason, "stop"))


async def call_openai(m, body, added, effort, usage):
    msgs = [dict(x) for x in body["messages"]]
    if added:
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = text_of(msgs[0].get("content")) + "\n\n" + added
        else:
            msgs.insert(0, {"role": "system", "content": added})
    payload = {**body, "model": m["model"], "messages": msgs, "stream": True,
               "stream_options": {"include_usage": True},
               "max_tokens": body.get("max_tokens") or LOCAL_MAX_TOKENS[effort]}
    async with http.stream("POST", m["url"] + "/chat/completions", json=payload) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if u := chunk.get("usage"):
                usage.update(input=u.get("prompt_tokens", 0), output=u.get("completion_tokens", 0))
            for ch in chunk.get("choices", []):
                if t := (ch.get("delta") or {}).get("content"):
                    yield t
                if ch.get("finish_reason"):
                    usage["finish"] = ch["finish_reason"]


# ---------- API ----------

app = FastAPI(title="router")


def log(rid, name, effort, task, prompt, added, usage, error):
    db.execute("insert into requests values (?,?,?,?,?,?,?,?,?,?)",
               (rid, time.time(), name, effort, json.dumps(task), prompt, added,
                usage.get("input", 0), usage.get("output", 0), error))
    db.commit()


def chunk(rid, name, delta, finish=None):
    return "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "created": int(time.time()),
                                  "model": name, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": n, "object": "model"} for n in ["router", *MODELS]]}


@app.get("/log")
def recent(n: int = 20):
    """Original prompt and the guidance the router added, side by side."""
    cur = db.execute("select * from requests order by ts desc limit ?", (n,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur]


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise HTTPException(400, "messages must be a non-empty list")
    prompt = next((text_of(x.get("content")) for x in reversed(msgs) if x.get("role") == "user"), "")
    ctx = sum(len(text_of(x.get("content"))) for x in msgs) // 4 + (body.get("max_tokens") or 0)
    task = await analyse(prompt)
    chain = [body["model"]] if body.get("model") in MODELS else rank(task, ctx)
    if not chain:
        raise HTTPException(503, f"no model fits a {task['task']} task within budget")

    rid, errors = "chatcmpl-" + uuid.uuid4().hex, []
    for name in chain:  # fallback chain: try the next model if this one can't start
        m = MODELS[name]
        effort, added, usage = choose_effort(task, name), rewrite(task, prompt, name), {}
        gen = (call_anthropic if m["backend"] == "anthropic" else call_openai)(m, body, added, effort, usage)
        try:
            first = await gen.__anext__()
        except StopAsyncIteration:
            first = ""
        except Exception as e:
            errors.append(f"{name}: {e!r}")
            log(rid, name, effort, task, prompt, added, usage, repr(e))
            continue
        break
    else:
        raise HTTPException(502, "all models failed: " + "; ".join(errors))

    headers = {"x-router-model": name, "x-router-task": task["task"], "x-router-effort": effort}

    if not body.get("stream"):
        error, parts = None, [first]
        try:
            parts += [t async for t in gen]
        except Exception as e:
            error = repr(e)
        log(rid, name, effort, task, prompt, added, usage, error)
        return JSONResponse(headers=headers, content={
            "id": rid, "object": "chat.completion", "created": int(time.time()), "model": name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "".join(parts)},
                         "finish_reason": usage.get("finish", "stop")}],
            "usage": {"prompt_tokens": usage.get("input", 0), "completion_tokens": usage.get("output", 0),
                      "total_tokens": usage.get("input", 0) + usage.get("output", 0)}})

    async def events():
        error = None
        yield chunk(rid, name, {"role": "assistant", "content": first})
        try:
            async for t in gen:
                yield chunk(rid, name, {"content": t})
        except Exception as e:  # mid-stream failure: too late to fall back, end the stream cleanly
            error = repr(e)
        log(rid, name, effort, task, prompt, added, usage, error)
        yield chunk(rid, name, {}, usage.get("finish", "stop"))
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
