"""Self-check against a fake upstream. Run: python test_router.py"""
import json
import os
import tempfile
import time

os.environ["ROUTER_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

import httpx
from fastapi.testclient import TestClient

import router

router.MODELS.clear()
router.MODELS.update({
    "big": {"backend": "openai", "url": "http://down/v1", "model": "b", "context": 10000,
            "quota_tokens": 1000, "prompt_style": "xml", "quality": {"coding": 9}},
    "small": {"backend": "openai", "url": "http://up/v1", "model": "s", "context": 10000,
              "prompt_style": "markdown", "quality": {"coding": 5, "quick_fact": 4}},
})
router.REG["analyser"] = "small"
sent = []


def upstream(req):
    body = json.loads(req.content)
    if body["messages"][0]["content"] == router.ANALYSER_PROMPT:
        reply = '{"task": "coding", "difficulty": 4, "importance": 2, "success_criteria": "runs", "inferred_context": ""}'
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    if req.url.host == "down":
        raise httpx.ConnectError("offline")
    sent.append(body)
    sse = "".join(f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n' for t in ["hello ", "world"])
    sse += 'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}\n\ndata: [DONE]\n\n'
    return httpx.Response(200, text=sse)


router.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
client = TestClient(router.app)


def demo():
    # analyser output is validated and clamped
    assert router.clean({"task": "nope", "difficulty": 9}) == {
        "task": "writing", "difficulty": 5, "importance": 2, "success_criteria": "", "inferred_context": ""}
    assert router.heuristic("fix this python function")["task"] == "coding"

    coding = router.clean({"task": "coding", "difficulty": 5, "importance": 3})
    assert router.rank(coding, 100) == ["big", "small"]
    assert router.rank(coding, 50000) == []  # context doesn't fit
    assert router.choose_effort(coding, "small") == "xhigh"
    assert router.choose_effort(router.clean({"task": "coding", "difficulty": 1, "importance": 1}), "small") == "low"

    assert '<skill name="python-code">' in router.rewrite(coding, "write python", "big")
    assert "## python-code" in router.rewrite(coding, "write python", "small")

    # streaming: big is offline -> falls back to small; user message reaches upstream unchanged
    with client.stream("POST", "/v1/chat/completions", json={
            "model": "router", "stream": True, "messages": [{"role": "user", "content": "write a python function"}]}) as r:
        assert r.headers["x-router-model"] == "small", r.headers
        lines = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: {")]
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in lines) == "hello world"
    up = sent[-1]["messages"]
    assert up[0]["role"] == "system" and "python-code" in up[0]["content"] and "runs" in up[0]["content"]
    assert up[1] == {"role": "user", "content": "write a python function"}
    log = client.get("/log").json()
    assert [row["model"] for row in log] == ["small", "big"] and log[1]["error"]
    assert log[0]["input_tokens"] == 7 and "python-code" in log[0]["added_system"]

    # non-streaming
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "write a python function"}]})
    assert r.json()["choices"][0]["message"]["content"] == "hello world"

    # reserve rule: big has 10% quota left -> only importance-3 work may use it, at capped effort
    router.db.execute("insert into requests (id, ts, model, input_tokens, output_tokens) values ('x', ?, 'big', 900, 0)",
                      (time.time(),))
    assert router.rank(router.clean({"task": "coding", "importance": 2}), 100) == ["small"]
    assert router.rank(coding, 100) == ["big", "small"]
    assert router.choose_effort(coding, "big") == "medium"

    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 400
    print("ok")


if __name__ == "__main__":
    demo()
