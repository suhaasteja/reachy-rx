# AGENTS.md — Reachy RX

## Project Overview

**Reachy RX** is an embodied AI pharmacist robot built on the [Reachy Mini](https://github.com/pollen-robotics/reachy-mini) platform. It combines a Vision-Language Model (VLM) vision loop with text-to-speech to act as a medication reminder assistant for elderly patients. The robot watches via camera, identifies people and medications, reminds patients to take their meds on schedule (sourced from a Google Sheet), verifies they're taking the right pills, and responds with expressive physical gestures (nodding, head-shaking, looking around) and spoken audio through Reachy's built-in speaker.

The robot persona is an upbeat, goofy pharmacist — like a cheerful nurse who cracks dad jokes while keeping patients on track with their medications.

## Architecture

```
Google Sheet (medication schedule)
  │
  ▼
MedicationReminder ← medication_taken.json (local persistence)
  │
Camera (Reachy USB / MacBook fallback)
  │
  ▼
main.py — vision loop (capture → context injection → VLM → execute actions → repeat)
  │
  ├── vlm_client.py            — Base VLM client, tool definitions, action execution
  ├── vlm_client_lmstudio.py   — LM Studio backend (text-parsed tool calls)
  ├── vlm_client_openai.py     — Standard OpenAI-compatible backend
  │
  ├── medication_reminder.py   — Schedule fetching (Google Sheets), due-med tracking, taken log
  ├── minimax_tts.py           — Direct Minimax HTTP TTS → Reachy speaker (no Agora)
  ├── sounds.py                — Synthesized audio (chirps, celebration) for speaker
  ├── system_prompt.md         — Robot persona & behavior instructions
  └── macbook_camera.py        — MacBook FaceTime camera fallback for dev

Agora Voice Agent (independent, optional)
  │
  ├── agora_voice_agent.py     — Starts cloud-hosted conversational AI agent
  ├── agora_voice_test.html    — Browser-based voice test client
  └── tts/client.py            — Agora RTC-based TTS (older approach, replaced by minimax_tts.py)
```

### Vision Loop (`main.py`)

The core loop runs continuously and sequentially (no overlapping frames):
1. Capture a frame from the camera
2. Build context: person presence state + medication reminders due now
3. Inject context into VLM prompt via `vlm.inject_context()`
4. Send frame + system prompt + action history to the VLM via `vlm.step()`
5. Parse the VLM response for text observations and tool calls
6. Execute tool calls on Reachy Mini hardware (head movements, speaker audio, TTS speech)
7. Wait for TTS to finish before capturing the next frame (avoids garbled audio)
8. Track person presence via keyword detection in VLM text output (state machine: appeared → greeted → left)

**CLI arguments:**
- `--debug` — Save frames to `debug_frames/` + verbose logging
- `--model NAME` — VLM model name (default: `nemotron-nano-12b-vl`)
- `--server URL` — VLM server base URL (default: Cloudflare tunnel)
- `--lmstudio` / `--no-lmstudio` — Toggle LM Studio vs OpenAI client (default: LM Studio)
- `--sheet-url URL` — Google Sheets URL for medication schedule

### VLM Client Hierarchy (`vlm_client.py`)

- **`BaseVLMClient`** (abstract) — Frame encoding (JPEG→base64), conversation history management (rolling window of 100), system prompt loading, context injection, async step support via `ThreadPoolExecutor`
- **`LMStudioVLMClient`** — Workaround for LM Studio's broken tool call parsing; describes tools in the system prompt and parses tool calls from raw text via regex (handles `name()`, `name({...})`, and `<tool_call>` XML patterns)
- **`OpenAIVLMClient`** — Standard client using the `tools=` API parameter; works with vLLM, Ollama, OpenAI, etc.

Currently active: **LM Studio** client (default `--lmstudio`) with `nemotron-nano-12b-vl` model via Cloudflare tunnel.

### Tool / Action System

The robot has 6 actions defined as OpenAI-format tool schemas:

| Tool | Description | Physical Behavior |
|------|-------------|-------------------|
| `nod_yes()` | Confirm / say yes | Head pitch up/down ×2 |
| `shake_no()` | Deny / signal concern | Head yaw left/right ×2 |
| `look_at(direction)` | Track patient: left/right/up/down/center | Head yaw/pitch to direction |
| `speak(message)` | Say something out loud through the speaker (TTS) | Minimax TTS → Reachy speaker |
| `remind_medication(name)` | Play escalating reminder chirp + gesture | 4 intensity levels with sounds + head animations + antenna poses |
| `mark_medication_taken(name, due_time)` | Record medication as taken | Celebration sound + happy wiggle dance + nod |

`speak()` is the **only** way the patient hears the robot — all other text is internal thinking.

`remind_medication()` has 4 escalating intensity levels based on nag count:
1. **Gentle** — soft chirp + head tilt
2. **Nudge** — double chirp + bouncy wiggle
3. **Insistent** — triple chirp + antenna flapping + pleading look-up
4. **URGENT** — alarm beeps + rapid wiggles + sad droop + hopeful perk up

Actions are executed in `execute_tool_calls()` which translates them into Reachy Mini head poses and antenna positions via `create_head_pose()`.

### Medication Reminder System (`medication_reminder.py`)

Reads the medication schedule from a **public Google Sheet** via the gviz JSON endpoint (no auth required). The sheet has columns: `Medication | Dosage | Form | Frequency | Times | Instructions | Condition`.

- **Schedule fetching**: Cached for 30 seconds, auto-refreshes on each check
- **Due detection**: Finds medications within ±15 minute window of scheduled time
- **Nag tracking**: Returns due meds every call with incrementing `nag_count` until `mark_taken()` is called
- **Taken persistence**: Records taken medications in `medication_taken.json` keyed by date and `MedName@HH:MM`
- **Context injection**: `main.py` injects reminders + thumbs-up detection instructions into the VLM prompt each frame

Default Google Sheet: `19DZLGsryVJVpGW-Vg1SRLFY2nNmMhFYPomM8s0RYOhE` (override via `--sheet-url`)

### Text-to-Speech (`minimax_tts.py`)

Direct HTTP calls to the **Minimax T2A v2 API** — no Agora RTC, no Node.js server needed:
1. POST text to `https://api.minimax.io/v1/t2a_v2` with voice `English_Upbeat_Woman`
2. Receive hex-encoded WAV audio in response
3. Decode WAV → float32, resample to 16kHz, boost volume 1.5×
4. Push PCM chunks (100ms each) to Reachy's speaker via `mini.media.push_audio_sample()`

Runs synthesis + playback on a daemon thread. Non-blocking `speak()`, drops requests if already speaking.

Requires env vars: `MINIMAX_TTS_KEY`, `MINIMAX_TTS_GROUP_ID`

### Sound Effects (`sounds.py`)

Synthesized audio clips generated with numpy (16kHz float32), pushed directly to Reachy's speaker:

| Sound | Function | Use Case |
|-------|----------|----------|
| Rising chirp | `chirp_up()` | Gentle reminder (nag level 1) |
| Double chirp | `double_chirp()` | Nudge reminder (nag level 2) |
| Triple chirp | `triple_chirp()` | Insistent reminder (nag level 3) |
| Alarm beeps | `alarm_beeps()` | Urgent reminder (nag level 4) |
| Celebration arpeggio | `celebration()` | Medication taken confirmation |

`get_reminder_sound(intensity)` maps nag count (1–4) to the appropriate sound.

### Agora Voice Agent (standalone, optional)

A separate **cloud-hosted conversational AI agent** for real-time voice interaction via Agora's Conversational AI platform. Runs independently of the vision loop — no shared context between them.

- `agora_voice_agent.py` — Starts the agent via Agora REST API (GPT-4o-mini + Minimax TTS + ASR)
- `agora_voice_test.html` — Browser client that joins the Agora RTC channel for voice interaction
- `start_reachy_full.sh` — Convenience script: starts voice agent → opens browser → instructions for vision loop

The agent joins channel `reachy_conversation` as UID `1000`, the browser client joins as UID `12345`.

Requires env vars: `AGORA_APP_ID`, `AGORA_RESTFUL_KEY`, `AGORA_RESTFUL_SECRET`

### System Prompt (`system_prompt.md`)

Defines the robot's persona and behavior rules:
- **Personality**: Upbeat, goofy, dad-joke-cracking nurse — genuinely caring but keeps things light
- **Face tracking**: Always call `look_at()` first to keep the patient centered
- **People state**: System injects 🆕/👤/🚫 context — greet new people once, don't re-greet
- **Core flow**: REMIND → VERIFY (read label, compare to due med) → CONFIRM (thumbs up → mark taken)
- **Safety**: NEVER accept wrong medication, always verify labels before marking taken
- **Response format**: `speak()` is the only patient-audible output; text outside `speak()` is internal thinking

### Legacy / Unused Components

| Path | Status | Notes |
|------|--------|-------|
| `tts/client.py` | **Replaced** | Agora RTC-based TTS client — replaced by `minimax_tts.py` (direct HTTP, simpler) |
| `tts/server.js` | **Replaced** | Node.js TTS server — no longer needed |
| `tts/index.html` | **Replaced** | TTS test page — superseded by `agora_voice_test.html` |
| `reachy-mini-agora-web-sdk/` | **Reference** | Agora Web SDK fork with agent manager, vision tools, etc. — not directly used by main app |

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — vision loop, camera init, Reachy connection, context injection, person state machine |
| `vlm_client.py` | Base client class, tool definitions (6 tools), `execute_tool_calls()` with gesture choreography |
| `vlm_client_lmstudio.py` | LM Studio VLM client with regex-based text tool call parsing |
| `vlm_client_openai.py` | Standard OpenAI-compatible VLM client |
| `medication_reminder.py` | Google Sheets schedule fetcher, due-med checker, taken-log persistence |
| `minimax_tts.py` | Direct Minimax HTTP TTS → Reachy speaker, non-blocking |
| `sounds.py` | Synthesized sound effects (chirps, celebration) at 16kHz |
| `macbook_camera.py` | MacBook FaceTime camera fallback for development |
| `system_prompt.md` | Robot persona, behavior rules, action examples |
| `medication_taken.json` | Persistent daily log of medications taken (gitignored) |
| `agora_voice_agent.py` | Standalone Agora cloud voice agent (GPT-4o-mini + Minimax TTS) |
| `agora_voice_test.html` | Browser-based Agora RTC voice test client |
| `start_reachy_full.sh` | Convenience script: voice agent + browser + vision loop instructions |
| `VOICE_TESTING.md` | Guide for testing the Agora voice agent |
| `.env.example` | Template for required environment variables |
| `pyproject.toml` | Project config — Python 3.11–3.12, deps listed below |

## Development Setup

- **Python**: 3.11–3.12 (managed via `.python-version`)
- **Package manager**: [uv](https://docs.astral.sh/uv/)
- **Dependencies**: `reachy-mini[mujoco]>=1.5.1`, `openai>=1.0.0`, `sounddevice>=0.5.5`, `soundfile>=0.13.1`, `requests>=2.31.0`, `python-dotenv>=1.0.0`, `agora-realtime-ai-api>=1.1.0`, `colorlog>=6.10.1`
- **VLM backend**: Any OpenAI-compatible server (LM Studio, vLLM, Ollama). Default: Cloudflare tunnel
- **Model**: `nemotron-nano-12b-vl` (Nvidia Nemotron Nano 12B VL)
- **TTS**: Minimax T2A v2 API (requires `MINIMAX_TTS_KEY` + `MINIMAX_TTS_GROUP_ID`)

### Environment Variables (`.env`)

```bash
# Minimax TTS (required for speech)
MINIMAX_TTS_KEY=your_api_key_here
MINIMAX_TTS_GROUP_ID=your_group_id_here

# Agora (only needed for standalone voice agent)
AGORA_APP_ID=your_app_id_here
AGORA_RESTFUL_KEY=your_restful_key_here
AGORA_RESTFUL_SECRET=your_restful_secret_here
AGORA_CHANNEL_TOKEN=
```

### Running

```bash
# 1. Install deps
uv sync

# 2. Start the Reachy Mini daemon (keep running in background)
uv run reachy-mini-daemon          # with robot (USB)
uv run reachy-mini-daemon --sim    # simulation (no robot)

# 3. Run the vision loop (new terminal)
uv run main.py                                          # defaults
uv run main.py --debug                                  # save frames + verbose
uv run main.py --model my-model --server http://host:8000/v1  # custom VLM
uv run main.py --sheet-url "https://docs.google.com/spreadsheets/d/..."  # custom schedule

# 4. (Optional) Standalone voice agent
python agora_voice_agent.py
open agora_voice_test.html
```

## Conventions

- **VLM backend switching**: `--lmstudio` (default) vs `--no-lmstudio` CLI flag controls which client is used
- **Camera fallback**: When no Reachy USB camera is found, auto-falls back to MacBook FaceTime camera
- **History management**: Rolling window of 100 action/observation entries to avoid repetitive behavior
- **Context injection**: `inject_context()` prepends situational context (person state, medication reminders) to the VLM prompt each frame, then clears it
- **Sequential execution**: Actions (including TTS) run to completion before the next frame — prevents overlapping audio
- **Nag escalation**: Reminder intensity increases each cycle a medication goes untaken (1=gentle → 4=urgent)
- **Taken persistence**: `medication_taken.json` stores daily records; medications marked taken stop generating reminders
- **Person state machine**: VLM text output is keyword-scanned to detect person appearing/leaving; greeting happens once per person visit
- **Frame debugging**: With `--debug`, raw frames saved to `debug_frames/` as timestamped JPEGs
- **Env loading**: `minimax_tts.py` self-loads `.env` from project root (no external dotenv dependency required for core loop)

## Branch Context

Current branch: `main`

## Coding Guidelines

- Type hints throughout (using `numpy.typing`, `Optional`, etc.)
- ABC pattern for VLM client extensibility — subclass `BaseVLMClient` and implement `_call_api()`
- OpenCV for all image handling (BGR format, JPEG encoding)
- OpenAI SDK types (`ChatCompletionMessageToolCall`) used as the standard tool call interface across all backends
- Keep `system_prompt.md` as the single source of truth for robot behavior — don't hardcode persona details in Python
- Sound effects are pure numpy — no external audio files, everything synthesized at runtime
- TTS is non-blocking (daemon threads) but the vision loop waits for completion before next frame
- Google Sheets integration uses the public gviz JSON endpoint — no API keys needed, just a shared sheet link
- Medication schedule is the Google Sheet; taken state is local JSON — separation of concerns
