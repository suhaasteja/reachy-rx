"""Forward OpenAI-style chat completions to a local Ollama instance.

Agora Conversational AI calls your LLM URL from Agora's cloud. `localhost` on your
machine is not reachable from Agora, so expose this proxy with ngrok, Cloudflare
Tunnel, Tailscale Funnel, or run it on a host with a public IP.

Ollama serves OpenAI-compatible POST /v1/chat/completions by default, e.g.:
  http://127.0.0.1:11434/v1/chat/completions

Usage:
  export OLLAMA_CHAT_COMPLETIONS_URL=http://127.0.0.1:11434/v1/chat/completions
  python scripts/ollama_openai_proxy.py

Or: uvicorn scripts.ollama_openai_proxy:app --host 0.0.0.0 --port 8100 --app-dir .

Then point agent_config `properties.llm.url` at your public URL, e.g.:
  https://<subdomain>.ngrok-free.app/v1/chat/completions

See agent_config.local_ollama.example.json in the repo root.
"""

from __future__ import annotations

import os

import requests
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

OLLAMA_CHAT_COMPLETIONS_URL = os.environ.get(
    "OLLAMA_CHAT_COMPLETIONS_URL",
    "http://127.0.0.1:11434/v1/chat/completions",
).strip()

app = FastAPI(title="Ollama OpenAI proxy", version="1.0.0")


def _forward_headers(request: Request) -> dict[str, str]:
    """Forward only headers Ollama cares about."""
    out: dict[str, str] = {}
    ct = request.headers.get("content-type")
    if ct:
        out["Content-Type"] = ct
    auth = request.headers.get("authorization")
    if auth:
        out["Authorization"] = auth
    accept = request.headers.get("accept")
    if accept:
        out["Accept"] = accept
    return out


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "true", "upstream": OLLAMA_CHAT_COMPLETIONS_URL}


@app.api_route("/v1/chat/completions", methods=["POST"])
@app.api_route("/chat/completions", methods=["POST"])
async def proxy_chat_completions(request: Request) -> StreamingResponse:
    body = await request.body()
    headers = _forward_headers(request)

    upstream = requests.post(
        OLLAMA_CHAT_COMPLETIONS_URL,
        data=body,
        headers=headers,
        stream=True,
        timeout=(10.0, None),
    )

    media_type = upstream.headers.get("content-type", "application/json")

    def iter_bytes() -> bytes:
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        iter_bytes(),
        status_code=upstream.status_code,
        media_type=media_type,
    )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BIND_HOST", "0.0.0.0").strip()
    port = int(os.environ.get("PORT", "8100"))
    uvicorn.run(app, host=host, port=port)
