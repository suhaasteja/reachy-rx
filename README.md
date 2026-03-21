# reachy-rx

Reachy Mini + Agora Conversational AI (simulator-first) workspace.

![arch](arch-reachy-rx.png)

This repo now uses the sponsor WebSDK app in `reachy-mini-agora-web-sdk/` for end-to-end voice testing.
The previous local FastAPI scaffold has been removed.

## Run With Reachy Simulator

1. Install dependencies in your active virtualenv:

```bash
cd reachy-mini-agora-web-sdk
pip install -e .
pip install "reachy-mini[mujoco]"
```

2. Create env file:

```bash
cp .env.example .env
```

3. Fill `.env`:
- `AGORA_APP_ID`
- `AGORA_API_KEY`
- `AGORA_API_SECRET`
- `AGORA_CHANNEL_NAME=reachy_conversation`
- `AGORA_Reachy_mini_USER_ID=12345`
- `AGORA_STRICT_REACHY_DEVICES=false` (important when no real hardware is attached)
- optional: `AGORA_APP_CERTIFICATE` (leave empty if your Agora project allows AppID mode)

4. Start simulator daemon (Terminal A):

```bash
mjpython -m reachy_mini.daemon.app.main --sim
```

Fallback if `mjpython` is unavailable:

```bash
reachy-mini-daemon --sim
```

5. Start web server (Terminal B):

```bash
cd reachy-mini-agora-web-sdk
python -m reachy_mini_agora_web_sdk.main --web-rtc-server
```

6. Open the app:

```bash
open http://localhost:8780
```

## Dependency Notes

- No root `requirements.txt` is needed for this workspace path.
- Dependencies are managed by the sponsor app in `reachy-mini-agora-web-sdk/pyproject.toml`.
