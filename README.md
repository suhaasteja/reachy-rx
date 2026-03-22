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

**Verify:** Open [http://localhost:8000/docs](http://localhost:8000/docs). If you see the Reachy SDK API docs, you're good.

### 3. Run the app

In a **new terminal**:
```bash
uv run main.py          # normal mode
uv run main.py --debug  # save frames + verbose logging
```

### VLM Backend

The app expects a VLM running locally. Start [LM Studio](https://lmstudio.ai/) with the `nvidia-nemotron-nano-12b-v2-vl` model loaded on `http://localhost:1234/v1`.
