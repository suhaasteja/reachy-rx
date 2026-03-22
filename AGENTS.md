# AGENTS.md — Reachy RX

## Project Overview

**Reachy RX** is an embodied AI pharmacist robot built on the [Reachy Mini](https://github.com/pollen-robotics/reachy-mini) platform. It uses a Vision-Language Model (VLM) in a continuous vision loop to identify medications held up by patients, read labels, log medication details, and respond with expressive physical gestures (nodding, head-shaking, looking around, emoting).

The robot persona is a curious, caring pharmacist that obsessively reads medication labels and provides helpful drug information to patients.

## Architecture

```
Camera (Reachy USB / MacBook fallback)
  │
  ▼
main.py — vision loop (capture frame → VLM → execute actions)
  │
  ├── vlm_client.py          — Base VLM client, tool definitions, action execution
  ├── vlm_client_lmstudio.py — LM Studio backend (text-parsed tool calls)
  ├── vlm_client_openai.py   — Standard OpenAI-compatible backend
  │
  ├── system_prompt.md        — Robot persona & behavior instructions
  ├── macbook_camera.py       — MacBook FaceTime camera fallback for dev
  └── medication_log.json     — Persistent JSON log of identified medications
```

### Vision Loop (`main.py`)

The core loop runs continuously:
1. Capture a frame from the camera
2. Send frame + system prompt + action history to the VLM
3. Parse the VLM response for text observations and tool calls
4. Execute tool calls on the Reachy Mini hardware (head movements, expressions)
5. Log medications to `medication_log.json` when identified

Supports `--debug` flag for saving frames to `debug_frames/` and verbose logging.

### VLM Client Hierarchy (`vlm_client.py`)

- **`BaseVLMClient`** (abstract) — Frame encoding (JPEG→base64), conversation history management (rolling window of 10), system prompt loading
- **`LMStudioVLMClient`** — Workaround for LM Studio's broken tool call parsing; describes tools in the system prompt and parses tool calls from raw text via regex
- **`OpenAIVLMClient`** — Standard client using the `tools=` API parameter; works with vLLM, Ollama, OpenAI, etc.

Currently active: **LM Studio** client with `nvidia-nemotron-nano-12b-v2-vl` model.

### Tool / Action System

The robot has 5 actions defined as OpenAI-format tool schemas:

| Tool | Description |
|------|-------------|
| `nod_yes()` | Nod head up/down — confirms understanding |
| `shake_no()` | Shake head side to side — can't read label or concern |
| `look_at(direction)` | Look left/right/up/down/center — track patient |
| `express_emotion(emotion)` | Express happy/sad/surprised/curious via body language |
| `log_medication(name, dosage, form, count, description)` | Log identified medication to JSON file + confirmation nod |

Actions are executed in `execute_tool_calls()` which translates them into Reachy Mini head poses via `create_head_pose()`.

### System Prompt (`system_prompt.md`)

Defines the robot's pharmacist persona: curious, warm, detail-obsessed, proactive about drug interactions and safety. Instructs the model to identify → log → care → ask → react → follow → idle.

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — vision loop, camera init, Reachy connection |
| `vlm_client.py` | Base client class, tool definitions, `execute_tool_calls()` |
| `vlm_client_lmstudio.py` | LM Studio VLM client with text-based tool call parsing |
| `vlm_client_openai.py` | Standard OpenAI-compatible VLM client |
| `macbook_camera.py` | MacBook FaceTime camera fallback for development |
| `system_prompt.md` | Robot persona and behavior instructions |
| `medication_log.json` | Persistent log of identified medications (gitignored) |
| `pyproject.toml` | Project config — Python 3.10–3.12, deps: `reachy-mini[mujoco]`, `openai` |

## Development Setup

- **Python**: 3.10–3.12 (managed via `.python-version`)
- **Package manager**: [uv](https://docs.astral.sh/uv/)
- **Dependencies**: `reachy-mini[mujoco]>=1.5.1`, `openai>=1.0.0`
- **VLM backend**: LM Studio running locally on `http://localhost:1234/v1`
- **Model**: `nvidia-nemotron-nano-12b-v2-vl`

```bash
uv sync
# Start LM Studio with the Nemotron VL model loaded
python main.py          # normal mode
python main.py --debug  # save frames + verbose tool call logging
```

## Conventions

- **VLM backend switching**: Toggle between LM Studio and OpenAI clients by changing the import in `main.py` (line 10–12)
- **Camera fallback**: When no Reachy hardware is connected, the system auto-falls back to the MacBook's FaceTime camera
- **History management**: The VLM client maintains a rolling window of the last 10 actions/observations to avoid repetitive behavior
- **Medication logging**: All identified medications are appended to `medication_log.json` with timestamps
- **Frame debugging**: With `--debug`, raw camera frames are saved to `debug_frames/` as timestamped JPEGs

## Branch Context

Current branch: `george/vlm-vision-loop` — Implements the core VLM vision loop with LM Studio workaround for tool call parsing.

## Coding Guidelines

- Type hints throughout (using `numpy.typing`, `Optional`, etc.)
- ABC pattern for VLM client extensibility — subclass `BaseVLMClient` and implement `_call_api()`
- OpenCV for all image handling (BGR format, JPEG encoding)
- OpenAI SDK types (`ChatCompletionMessageToolCall`) used as the standard tool call interface across all backends
- Keep `system_prompt.md` as the single source of truth for robot behavior — don't hardcode persona details in Python
