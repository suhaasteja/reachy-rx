# Vision / local VLM implementation plan (v1)

**Status:** Implemented — `action_type` **`vision_read`**, module `reachy_mini_agora_web_sdk.vision.ollama_vlm`, wiring in `web_datastream_processor.py`, prompt updates in `prompt.txt`. See **[OLLAMA_SETUP.md](OLLAMA_SETUP.md)** for Ollama install.

---

## Goals (v1)

- User says something like “hey, can you read this” while in normal **ConvoAI** voice session.
- **LLM** (same agent, via `_publish_message`) emits a **new `action_type`** reserved for vision.
- **Python backend** captures **one JPEG** from the **Mac webcam**, calls **Ollama** OpenAI-compatible chat API with **image + short instruction**, writes the **assistant text** to **logs**.
- **Non-goals v1**: Jetson, multi-frame, injecting VLM text back into agent TTS, UI for vision result.

---

## Architecture (confirmed)

1. **ASR** turns user speech into **text** inside Agora ConvoAI (no extra STT in our app for tool intent).
2. **LLM** chooses tool: `_publish_message` with JSON `content` string containing **`action_type`** (see `prompt.txt` / `web_datastream_processor.py`).
3. **Datastream** delivers payload to backend → **`WebDatastreamProcessor`** maps `action_type` → local handler.
4. **New path**: `action_type` e.g. `vision_read` → **vision module**:
   - Grab **one** frame from default webcam (OpenCV or imageio; configurable device index via env).
   - Encode JPEG.
   - `POST` to `http://127.0.0.1:11434/v1/chat/completions` (or `OLLAMA_BASE_URL`) with:
     - `model`: from env (e.g. `llava` / whatever user `ollama pull`’d).
     - `messages`: user message with **multimodal** content (`image_url` data URI or base64 per OpenAI + Ollama docs).
   - Log result at **INFO** (and full request id / timing optional).

---

## Config / environment

| Variable | Purpose |
|----------|---------|
| `OLLAMA_BASE_URL` | Default `http://127.0.0.1:11434/v1` (no trailing slash ambiguity — normalize in code). |
| `OLLAMA_VISION_MODEL` | e.g. `llava` or user’s vision model name. |
| `VISION_WEBCAM_INDEX` | Default `0`. |
| `VISION_ENABLED` | Default `true`; if `false`, handler logs and no-ops (optional safety). |

---

## Files to add

| File | Role |
|------|------|
| `src/reachy_mini_agora_web_sdk/vision/ollama_vlm.py` | `capture_webcam_jpeg()` + `describe_image_with_ollama(jpeg_bytes, prompt)` using `requests`. |
| `src/reachy_mini_agora_web_sdk/vision/__init__.py` | Package marker; export minimal public API. |

## Files to change

| File | Change |
|------|--------|
| `src/reachy_mini_agora_web_sdk/web_datastream_processor.py` | In `_map_action_to_tool` or parallel path: **`vision_read`** → async handler that does **not** use existing `Tool`/`ReachyMini` dance tools — call vision module, return `{"ok": true, "logged": true}`. |
| `prompt.txt` | Document **`vision_read`** `action_type`, when to fire (user asks to read / see / what’s on screen), and that **spoken reply** should stay natural; **no** reading raw JSON aloud (same safety rules as other tools). |
| `README.md` (short subsection) | Optional: env vars + “pull a vision model in Ollama” one-liner. **Only if** we keep docs minimal (bullet list). |

---

## Implementation order (for coding)

1. **`ollama_vlm.py`**: single snapshot + one HTTP non-streaming completion (simplest); robust error logging (connection refused, 4xx/5xx).
2. **`web_datastream_processor.py`**: detect `action_type == "vision_read"` in payload; **await** vision call (may need `asyncio.to_thread` if using sync `requests`).
3. **`prompt.txt`**: add `vision_read` schema and behavior.
4. **Manual test**: run Ollama + web server; simulate or trigger tool via **real** voice phrase; confirm logs show VLM text.

---

## Testing checklist

- [ ] `ollama list` shows a vision-capable model; `curl` to `/v1/chat/completions` with a test image works outside the app.
- [ ] With app running, triggering **vision_read** produces **one** log line (or structured log) with description text.
- [ ] Failure modes: Ollama down → logged error, no crash; webcam busy → logged error.

---

## Future (out of scope for this plan)

- **Jetson**: same module; change `OLLAMA_BASE_URL` to LAN IP.
- **Multi-frame**: burst capture + merge or multi-image message.
- **TTS / agent context**: Agora **speak** or **update** / history injection so the agent says “I read …” using VLM output.
- **Reachy camera** instead of Mac webcam: swap capture backend; same Ollama client.

---

## Resolved

- **`action_type`**: `vision_read`.
- **Dispatch**: handled in **`WebDatastreamProcessor`** (not `core_tools`), so it runs **without** Reachy `ToolDependencies` (simulator-friendly).
