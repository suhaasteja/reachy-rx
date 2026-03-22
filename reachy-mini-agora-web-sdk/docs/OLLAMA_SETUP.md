# Ollama setup (Mac, vision / VLM)

Used by **`vision_read`** in the Web RTC server: one webcam frame → **Ollama** OpenAI-compatible `/v1/chat/completions` → log line.

## 1. Install Ollama

- Install the macOS app from [https://ollama.com](https://ollama.com) (or `brew install ollama` if you use Homebrew).
- Start Ollama so it listens on **`http://127.0.0.1:11434`** (default).

## 2. Pull a vision model

Examples (pick one; `llava` is a common default):

```bash
ollama pull llava
# or, e.g.:
# ollama pull llava:13b
# ollama pull moondream
```

List models:

```bash
ollama list
```

## 3. Quick API check (optional)

With Ollama running:

```bash
curl -sS http://127.0.0.1:11434/api/tags | head
```

## 4. Environment variables (project)

Set in `.env` next to `agent_config.json` or export in the shell before starting the app:

| Variable | Default | Meaning |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible API base (no trailing slash issues — code normalizes). |
| `OLLAMA_VISION_MODEL` | `llava` | Model name as shown by `ollama list`. |
| `VISION_WEBCAM_INDEX` | `0` | macOS webcam index (`0` = default camera). |
| `VISION_ENABLED` | `true` | Set to `false` to disable vision actions without code changes. |
| `VISION_SPEAK_ENABLED` | `true` | After VLM, call Agora **`/speak`** so the agent **reads** the text (max **512 bytes**). |
| `VISION_CONTEXT_APPEND_ENABLED` | `true` | Call Agora **`/update`** to append a **system** message with the latest observation (rolling: replaces prior “Latest camera observation” line). |
| `VISION_SPEAK_PRIORITY` | `APPEND` | `INTERRUPT` \| `APPEND` \| `IGNORE` — Agora **Broadcast a message using TTS** (`/agents/{id}/speak`). |

**Note:** `/speak` is **not** supported if your agent uses **MLLM-only** config (see Agora docs). Standard ASR+LLM+TTS agents are fine.

## 5. Run with Reachy simulator + Web RTC

1. Start **Reachy simulator** / daemon as you already do.
2. Start **Ollama** (menu bar app or `ollama serve`).
3. Activate venv and run the Web RTC server:

```bash
cd /path/to/reachy-mini-agora-web-sdk
source reachy_mini_env/bin/activate
pip install -e .
python -m reachy_mini_agora_web_sdk.main --web-rtc-server
```

4. Open the web UI, talk to the agent; when you ask it to read/see something, it should emit **`vision_read`**; watch the **server terminal** for `VISION_READ result: ...`.

## 6. Jetson tomorrow

Point **`OLLAMA_BASE_URL`** at the Jetson LAN URL, e.g. `http://192.168.1.50:11434/v1`, and ensure the Mac can reach that host (same Wi‑Fi / LAN).
