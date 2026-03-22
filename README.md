# reachy-rx

![arch](arch-reachy-rx.png)

## Setup

### 1. Install dependencies

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 2. Start the Reachy Mini Daemon

The daemon is a background server that handles low-level communication with motors and sensors. It must be running before you launch the app.

**With robot (USB):**
```bash
uv run reachy-mini-daemon
```

**Simulation (no robot needed):**
```bash
uv run reachy-mini-daemon --sim
```

> **Note:** Keep the daemon terminal open. It must stay running while the app is active.

### 3. Run the app

In a **new terminal**:
```bash
uv run main.py          # normal mode
uv run main.py --debug  # save frames + verbose logging

# custom model/server
uv run main.py --model my-model --server http://host:8000/v1

# use LM Studio backend (text-parsed tool calls)
uv run main.py --lmstudio
```

### VLM Backend

The app expects an OpenAI-compatible VLM server. By default it connects to `http://localhost:1234/v1` with model `nvidia-nemotron-nano-12b-v2-vl`. Override with `--model` and `--server`.

Pass `--lmstudio` to use the LM Studio client instead. This works around LM Studio's broken tool call parsing by describing tools in the system prompt and extracting calls from the model's text output.
