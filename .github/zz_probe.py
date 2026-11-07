import json, os, re, urllib.request, base64
base = os.environ["BASE"].rstrip("/"); key = os.environ["KEY"]
print("host:", re.sub(r"https?://([^/]+).*", r"\1", base), "| path segments:", base.split("/")[3:5], "... depth", len(base.split("/")) - 3)
def call(method, url, payload=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
for p in ("models", "v1/models"):
    code, body = call("GET", f"{base}/{p}")
    print("== GET", p, code, len(body))
    try:
        d = json.loads(body); items = d.get("data") if isinstance(d, dict) else d
        ids = [i.get("id") for i in items] if isinstance(items, list) else []
        print("count", len(ids)); print([i for i in ids if re.search(r"vl|vision|gpt-4|gpt-5|gemini|claude|qwen|glm|pixtral|llama|kimi|doubao", str(i), re.I)][:80])
    except Exception: print("not json:", body[:200])
# test text inference works
for m in ["deepseek/deepseek-v4.1-flash"]:
    pl = {"model": m, "max_tokens": 10, "messages": [{"role": "user", "content": "say hi"}]}
    c, b = call("POST", f"{base}/chat/completions", pl)
    try: r = json.loads(b)["choices"][0]["message"]["content"]
    except Exception: r = b[:240]
    print("== text", m, c, r)
# tiny red png 2x2
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMAAAAUAAQ0E1KcAAAAASUVORK5CYII=")
for model in os.environ.get("CANDIDATES", "").split():
    payload = {"model": model, "max_tokens": 50, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What color is this image? one word"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    code, body = call("POST", f"{base}/chat/completions", payload)
    try: msg = json.loads(body)["choices"][0]["message"]["content"]
    except Exception: msg = body[:240]
    print("== vision", model, code, msg)
