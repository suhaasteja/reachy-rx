<div align="center">

# Reachy RX

**An embodied AI pharmacist robot that watches, reminds, and cares.**

Built on [Reachy Mini](https://github.com/pollen-robotics/reachy-mini) · Powered by [NVIDIA Nemotron Nano VL](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8) · Voice by [MiniMax TTS](https://www.minimaxi.com/)

![arch](arch-reachy-rx.png)

</div>

---

Reachy RX is an embodied AI pharmacist that helps elderly patients take the right medications on time. It uses a camera to watch for people and medication bottles, a vision-language model to understand what it sees, and text-to-speech to talk through the robot's speaker, all while expressing itself with head gestures, antenna wiggles, and synthesized sound effects.

The robot persona is an upbeat, goofy pharmacist, like a cheerful nurse who cracks dad jokes while keeping patients safe.

## Setup

### 1. Install dependencies

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 2. Configure environment

Copy the example env and fill in your keys:

```bash
cp .env.example .env
```

```bash
# Required for speech
MINIMAX_TTS_KEY=your_api_key_here
MINIMAX_TTS_GROUP_ID=your_group_id_here

# Only needed for the standalone voice agent (optional)
AGORA_APP_ID=your_app_id_here
AGORA_RESTFUL_KEY=your_restful_key_here
AGORA_RESTFUL_SECRET=your_restful_secret_here
```

### 3. Start the Reachy Mini Daemon

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

### 4. Run the app

In a **new terminal**:
```bash
uv run main.py          # normal mode
uv run main.py --debug  # save frames + verbose logging

# custom model/server
uv run main.py --model my-model --server http://host:8000/v1

# custom medication schedule
uv run main.py --sheet-url "https://docs.google.com/spreadsheets/d/..."
```

### VLM Backend

The vision-language model is **[NVIDIA Nemotron Nano VL 12B V2 (FP8)](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8)**, running on an NVIDIA L40S GPU (48 GiB) hosted on [Brev](https://brev.dev/) and accessed via an OpenAI-compatible API through a Cloudflare tunnel.

- **Model**: `nemotron-nano-12b-vl`, a 13B parameter vision-language model with C-RADIOv2 vision encoder
- **Quantization**: FP8 for fast inference on NVIDIA GPUs
- **GPU**: NVIDIA L40S (48 GiB) hosted on [Brev](https://brev.dev/)
- **Capabilities**: Image understanding, OCR, visual Q&A, tool/function calling
- **Serving**: vLLM with OpenAI-compatible API endpoints

Override the defaults with `--model` and `--server` flags.

Pass `--lmstudio` to use the LM Studio client (default), which works around tool call parsing issues by describing tools in the system prompt and extracting calls from the model's text output via regex. Use `--no-lmstudio` for servers with native structured tool call support (vLLM, Ollama, OpenAI).

### Text-to-Speech

Speech is handled by **[MiniMax T2A v2](https://www.minimaxi.com/)**, a cloud TTS API that produces natural-sounding speech.

- **Model**: `speech-2.6-turbo`
- **Voice**: `English_Upbeat_Woman`, matches the robot's cheerful persona
- **Flow**: Text → MiniMax HTTP API → hex-encoded WAV → decode → resample to 16kHz → push PCM to Reachy's speaker
- **Behavior**: Non-blocking (daemon thread), drops requests if already speaking

Requires `MINIMAX_TTS_KEY` and `MINIMAX_TTS_GROUP_ID` in your `.env` file.

---

## Architecture Deep Dive

### What is Reachy RX?

Reachy RX turns a [Reachy Mini](https://github.com/pollen-robotics/reachy-mini) desktop robot into an **autonomous medication reminder assistant**. It's designed for elderly patients who may forget to take their pills on time, a real problem that leads to [over 100,000 preventable deaths per year](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3934668/) in the US alone.

Instead of a phone alarm that's easy to ignore, Reachy RX is a physical presence that:

- **Watches** for the patient through a camera
- **Knows** the medication schedule (pulled live from a Google Sheet)
- **Reminds** with escalating urgency, gentle chirps at first, alarm beeps if ignored
- **Verifies** the patient is taking the *right* medication by reading bottle labels
- **Confirms** with a thumbs-up gesture check before marking meds as taken
- **Celebrates** when medications are taken, happy wiggles and all

### Why Build This?

Medication non-adherence is one of the biggest problems in elder care. Existing solutions (phone alarms, pill organizers, smart dispensers) are either too easy to ignore or too expensive and complex. A robot with a face, a voice, and a personality is much harder to dismiss, and the dad jokes don't hurt either.

The key insight: **a medication reminder needs to be persistent AND likeable**. Reachy RX escalates from a gentle chirp to an urgent alarm, but always with a warm personality. It's the difference between a nagging phone notification and a friendly nurse who genuinely cares.

### System Overview

<!--
  Architecture diagram (SVG)
  Shows the three main data flows: Schedule, Vision, and Actions
-->

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 520" font-family="Inter, system-ui, -apple-system, sans-serif">
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#6366f1"/>
    </marker>
    <marker id="arrow-green" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#22c55e"/>
    </marker>
    <marker id="arrow-amber" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#f59e0b"/>
    </marker>
    <filter id="shadow">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.1"/>
    </filter>
  </defs>

  <!-- Background -->
  <rect width="900" height="520" rx="12" fill="#fafbfc"/>

  <!-- Title -->
  <text x="450" y="35" text-anchor="middle" font-size="18" font-weight="700" fill="#1e293b">Reachy RX - System Architecture</text>

  <!-- Google Sheet (external) -->
  <rect x="30" y="60" width="160" height="56" rx="10" fill="#e0f2fe" stroke="#38bdf8" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="110" y="84" text-anchor="middle" font-size="11" font-weight="600" fill="#0369a1">Google Sheet</text>
  <text x="110" y="100" text-anchor="middle" font-size="9" fill="#64748b">Medication Schedule</text>

  <!-- MedicationReminder -->
  <rect x="30" y="145" width="160" height="70" rx="10" fill="#fff" stroke="#94a3b8" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="110" y="168" text-anchor="middle" font-size="11" font-weight="600" fill="#1e293b">MedicationReminder</text>
  <text x="110" y="183" text-anchor="middle" font-size="9" fill="#64748b">Fetch schedule (gviz API)</text>
  <text x="110" y="196" text-anchor="middle" font-size="9" fill="#64748b">Track due meds + nag count</text>
  <text x="110" y="209" text-anchor="middle" font-size="9" fill="#64748b">Persist taken log (JSON)</text>

  <!-- Arrow: Sheet → Reminder -->
  <line x1="110" y1="116" x2="110" y2="143" stroke="#6366f1" stroke-width="1.5" marker-end="url(#arrow)"/>

  <!-- Camera -->
  <rect x="30" y="260" width="160" height="50" rx="10" fill="#fef3c7" stroke="#f59e0b" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="110" y="282" text-anchor="middle" font-size="11" font-weight="600" fill="#92400e">Camera</text>
  <text x="110" y="298" text-anchor="middle" font-size="9" fill="#64748b">Reachy USB / MacBook fallback</text>

  <!-- Main Loop (center) -->
  <rect x="270" y="120" width="240" height="210" rx="14" fill="#f0f9ff" stroke="#6366f1" stroke-width="2" filter="url(#shadow)"/>
  <text x="390" y="148" text-anchor="middle" font-size="13" font-weight="700" fill="#4338ca">Vision Loop</text>
  <text x="390" y="166" text-anchor="middle" font-size="9" fill="#64748b">main.py - runs continuously</text>
  <line x1="290" y1="178" x2="490" y2="178" stroke="#c7d2fe" stroke-width="1"/>
  <text x="300" y="198" font-size="10" fill="#334155">1. Capture frame</text>
  <text x="300" y="216" font-size="10" fill="#334155">2. Build context (meds + people)</text>
  <text x="300" y="234" font-size="10" fill="#334155">3. Inject context → VLM prompt</text>
  <text x="300" y="252" font-size="10" fill="#334155">4. Send frame + prompt to VLM</text>
  <text x="300" y="270" font-size="10" fill="#334155">5. Parse response → tool calls</text>
  <text x="300" y="288" font-size="10" fill="#334155">6. Execute actions sequentially</text>
  <text x="300" y="306" font-size="10" fill="#334155">7. Wait for TTS → next frame</text>

  <!-- Arrow: Reminder → Main Loop -->
  <line x1="190" y1="180" x2="268" y2="180" stroke="#6366f1" stroke-width="1.5" marker-end="url(#arrow)"/>
  <text x="229" y="173" text-anchor="middle" font-size="8" fill="#6366f1">context</text>

  <!-- Arrow: Camera → Main Loop -->
  <line x1="190" y1="285" x2="268" y2="260" stroke="#f59e0b" stroke-width="1.5" marker-end="url(#arrow-amber)"/>
  <text x="229" y="265" text-anchor="middle" font-size="8" fill="#f59e0b">frame</text>

  <!-- VLM Cloud (NVIDIA) -->
  <rect x="600" y="80" width="260" height="90" rx="12" fill="#f0fdf4" stroke="#22c55e" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="730" y="104" text-anchor="middle" font-size="11" font-weight="700" fill="#166534">NVIDIA Brev VLM</text>
  <text x="730" y="120" text-anchor="middle" font-size="9" fill="#64748b">Nemotron Nano VL 12B V2 (FP8)</text>
  <text x="730" y="135" text-anchor="middle" font-size="9" fill="#64748b">vLLM · OpenAI-compat API</text>
  <text x="730" y="150" text-anchor="middle" font-size="9" fill="#64748b">Image + text → text + tool calls</text>
  <text x="730" y="165" text-anchor="middle" font-size="9" fill="#475569">L40S 48 GiB · Cloudflare Tunnel</text>

  <!-- Arrow: Main → VLM -->
  <line x1="510" y1="165" x2="598" y2="135" stroke="#22c55e" stroke-width="1.5" marker-end="url(#arrow-green)"/>
  <text x="554" y="140" text-anchor="middle" font-size="8" fill="#22c55e">frame+prompt</text>

  <!-- Arrow: VLM → Main -->
  <line x1="598" y1="155" x2="510" y2="195" stroke="#22c55e" stroke-width="1.5" marker-end="url(#arrow-green)"/>
  <text x="554" y="184" text-anchor="middle" font-size="8" fill="#22c55e">response</text>

  <!-- MiniMax TTS -->
  <rect x="600" y="200" width="260" height="70" rx="12" fill="#fdf2f8" stroke="#ec4899" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="730" y="224" text-anchor="middle" font-size="11" font-weight="700" fill="#9d174d">MiniMax TTS</text>
  <text x="730" y="240" text-anchor="middle" font-size="9" fill="#64748b">speech-2.6-turbo · English_Upbeat_Woman</text>
  <text x="730" y="256" text-anchor="middle" font-size="9" fill="#64748b">Text → WAV → resample 16kHz → speaker</text>

  <!-- Arrow: Main → TTS -->
  <line x1="510" y1="240" x2="598" y2="235" stroke="#ec4899" stroke-width="1.5" stroke-dasharray="5,3" marker-end="url(#arrow)"/>
  <text x="554" y="230" text-anchor="middle" font-size="8" fill="#ec4899">speak()</text>

  <!-- Reachy Hardware -->
  <rect x="600" y="310" width="260" height="90" rx="12" fill="#fff7ed" stroke="#f97316" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="730" y="334" text-anchor="middle" font-size="11" font-weight="700" fill="#9a3412">Reachy Mini Hardware</text>
  <text x="730" y="352" text-anchor="middle" font-size="9" fill="#64748b">Head: pitch, yaw, roll via servo motors</text>
  <text x="730" y="367" text-anchor="middle" font-size="9" fill="#64748b">Antennas: expressive positioning</text>
  <text x="730" y="382" text-anchor="middle" font-size="9" fill="#64748b">Speaker: 16kHz PCM audio output</text>

  <!-- Arrow: Main → Reachy -->
  <line x1="510" y1="300" x2="598" y2="340" stroke="#f97316" stroke-width="1.5" marker-end="url(#arrow-amber)"/>
  <text x="554" y="315" text-anchor="middle" font-size="8" fill="#f97316">gestures + audio</text>

  <!-- Sound Effects -->
  <rect x="270" y="370" width="240" height="60" rx="10" fill="#faf5ff" stroke="#a855f7" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="390" y="394" text-anchor="middle" font-size="11" font-weight="600" fill="#7e22ce">Synthesized Sound Effects</text>
  <text x="390" y="410" text-anchor="middle" font-size="9" fill="#64748b">Chirps (gentle→urgent) · Celebration arpeggio</text>
  <text x="390" y="424" text-anchor="middle" font-size="9" fill="#64748b">Pure numpy @ 16kHz, no audio files</text>

  <!-- Arrow: Sounds → Reachy -->
  <line x1="510" y1="400" x2="598" y2="380" stroke="#a855f7" stroke-width="1.5" stroke-dasharray="5,3" marker-end="url(#arrow)"/>

  <!-- Taken Log -->
  <rect x="30" y="370" width="160" height="50" rx="10" fill="#f5f5f4" stroke="#a8a29e" stroke-width="1.5" filter="url(#shadow)"/>
  <text x="110" y="392" text-anchor="middle" font-size="11" font-weight="600" fill="#57534e">medication_taken.json</text>
  <text x="110" y="408" text-anchor="middle" font-size="9" fill="#64748b">Daily persistence · keyed by date</text>

  <!-- Arrow: Main → Taken Log -->
  <line x1="270" y1="330" x2="190" y2="378" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="4,3" marker-end="url(#arrow)"/>
  <text x="225" y="348" text-anchor="middle" font-size="8" fill="#94a3b8">mark taken</text>

  <!-- Legend -->
  <rect x="30" y="455" width="840" height="50" rx="8" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
  <text x="50" y="475" font-size="9" font-weight="600" fill="#64748b">DATA FLOWS:</text>
  <line x1="120" y1="472" x2="160" y2="472" stroke="#6366f1" stroke-width="2"/>
  <text x="168" y="476" font-size="9" fill="#64748b">Context injection</text>
  <line x1="260" y1="472" x2="300" y2="472" stroke="#22c55e" stroke-width="2"/>
  <text x="308" y="476" font-size="9" fill="#64748b">VLM inference</text>
  <line x1="390" y1="472" x2="430" y2="472" stroke="#f59e0b" stroke-width="2"/>
  <text x="438" y="476" font-size="9" fill="#64748b">Frames/Gestures</text>
  <line x1="540" y1="472" x2="580" y2="472" stroke="#ec4899" stroke-width="2" stroke-dasharray="5,3"/>
  <text x="588" y="476" font-size="9" fill="#64748b">Audio</text>
  <line x1="640" y1="472" x2="680" y2="472" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4,3"/>
  <text x="688" y="476" font-size="9" fill="#64748b">Persistence</text>

  <text x="50" y="495" font-size="9" font-weight="600" fill="#64748b">EXTERNAL:</text>
  <rect x="112" y="487" width="10" height="10" rx="2" fill="#e0f2fe" stroke="#38bdf8" stroke-width="1"/>
  <text x="130" y="496" font-size="9" fill="#64748b">Google Sheets</text>
  <rect x="212" y="487" width="10" height="10" rx="2" fill="#f0fdf4" stroke="#22c55e" stroke-width="1"/>
  <text x="230" y="496" font-size="9" fill="#64748b">NVIDIA Brev</text>
  <rect x="310" y="487" width="10" height="10" rx="2" fill="#fdf2f8" stroke="#ec4899" stroke-width="1"/>
  <text x="328" y="496" font-size="9" fill="#64748b">MiniMax API</text>
</svg>
```

### How the Vision Loop Works

The core of Reachy RX is a **sequential vision loop** in `main.py`. It runs one iteration at a time, no overlapping frames, no parallel audio, to keep things simple and prevent garbled speech.

Here's what happens on every cycle:

```
┌─────────────────────────────────────────────────────┐
│                    VISION LOOP                      │
│                                                     │
│  ┌──────────┐    Is this a new person?              │
│  │ Grab  │    Any medications due right now?     │
│  │  Frame   │──▶ What meds were already taken?      │
│  └──────────┘    Build a context string from all    │
│       │          of this and inject it.             │
│       ▼                                             │
│  ┌──────────────────────────┐                       │
│  │ Send to VLM          │                       │
│  │                          │                       │
│  │  System prompt (persona) │                       │
│  │  + injected context      │                       │
│  │  + camera frame          │                       │
│  │  + action history        │                       │
│  └──────────────────────────┘                       │
│       │                                             │
│       ▼                                             │
│  ┌──────────────────────────┐                       │
│  │ Parse Response        │                       │
│  │                          │                       │
│  │  Text (internal thought) │                       │
│  │  + Tool calls (actions)  │                       │
│  └──────────────────────────┘                       │
│       │                                             │
│       ▼                                             │
│  ┌──────────────────────────┐                       │
│  │ Execute Actions       │                       │
│  │                          │                       │
│  │  nod_yes / shake_no      │                       │
│  │  look_at(direction)      │                       │
│  │  speak(message) → TTS    │                       │
│  │  remind_medication(name) │                       │
│  │  mark_medication_taken() │                       │
│  └──────────────────────────┘                       │
│       │                                             │
│       ▼                                             │
│  Wait for TTS to finish (up to 20s)             │
│       │                                             │
│       ▼                                             │
│  Track person presence state → next frame        │
└─────────────────────────────────────────────────────┘
```

**Why sequential?** Overlapping frames while audio is playing leads to the VLM seeing a "speaking robot" state and generating contradictory actions. Running one complete cycle at a time keeps behavior predictable.

### Where the Schedule Comes From

The medication schedule lives in a **Google Sheet**, just a shared spreadsheet that a caregiver or pharmacist can edit from anywhere. No database, no custom backend.

| Medication | Dosage | Form | Frequency | Times | Instructions | Condition |
|---|---|---|---|---|---|---|
| Lisinopril | 10mg | Tablet | Once daily | 08:00 | Take with water | Hypertension |
| Omeprazole | 20mg | Capsule | Once daily | 07:30 | Before breakfast | Acid reflux |
| Metformin | 500mg | Tablet | Twice daily | 08:00,18:00 | Take with food | Diabetes |

The system reads this via Google's `gviz` JSON endpoint, a lightweight way to pull structured data from Sheets without a full API integration.

**How reminders work:**

1. **Every loop cycle**, `MedicationReminder.check_and_remind()` checks the schedule
2. Medications within a **±15 minute window** of their scheduled time are flagged as "due"
3. Each due med gets a **nag count** that increments every cycle
4. The nag count drives **escalating urgency** (see below)
5. When the patient gives a thumbs up, `mark_medication_taken()` persists it to `medication_taken.json`
6. Once marked taken, that med stops generating reminders for the rest of the day
7. Schedule is **cached for 30 seconds** to avoid hammering Google's servers

### Escalating Reminders

Reachy doesn't just remind once and give up. It gets increasingly animated:

| Level | Nag Count | Sound | Gesture | Mood |
|:---:|:---:|---|---|---|
| 🟢 | 1 | Gentle chirp ↗ | Soft head tilt + curious antenna perk | "Hey, just a reminder..." |
| 🟡 | 2 | Double chirp ↗↗ | Bouncy side-to-side wiggle | "C'mon, time for your meds!" |
| 🟠 | 3 | Triple chirp ↗↗↗ | Wiggles + antenna flapping + look-up plea | "Please? Pretty please?" |
| 🔴 | 4+ | Alarm beeps | Rapid wiggles → sad droop → hopeful perk-up | "I'm REALLY worried now!" |

All sounds are **synthesized with numpy** at runtime, no audio files. Pure math generating chirps, arpeggios, and alarm tones at 16kHz.

### The Robot's Actions (Tool System)

The VLM controls Reachy through **6 tool calls** defined as OpenAI-format function schemas:

| Tool | What It Does | Physical Effect |
|---|---|---|
| `nod_yes()` | Confirm / say yes | Head pitch up/down ×2 |
| `shake_no()` | Deny / signal concern | Head yaw left/right ×2 |
| `look_at(direction)` | Track patient position | Head turns to left/right/up/down/center |
| `speak(message)` | **Talk to the patient** (only audible output) | MiniMax TTS → WAV → Reachy speaker |
| `remind_medication(name)` | Play reminder chirp + gesture | Escalating animation based on nag count |
| `mark_medication_taken(name, due_time)` | Record med as taken | Celebration sound + happy wiggle dance |

> **Important:** `speak()` is the **only** way the patient hears the robot. Everything else the VLM outputs is internal thinking. If the model doesn't call `speak()`, the patient hears nothing.

### The Core Flow: Remind → Verify → Confirm

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 240" font-family="Inter, system-ui, -apple-system, sans-serif">
  <defs>
    <marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#6366f1"/>
    </marker>
  </defs>

  <rect width="780" height="240" rx="10" fill="#fafbfc"/>

  <!-- Step 1: REMIND -->
  <rect x="20" y="30" width="200" height="170" rx="12" fill="#fef3c7" stroke="#f59e0b" stroke-width="2"/>
  <text x="120" y="58" text-anchor="middle" font-size="14" font-weight="700" fill="#92400e">1. REMIND</text>
  <line x1="40" y1="68" x2="200" y2="68" stroke="#fbbf24" stroke-width="1"/>
  <text x="120" y="88" text-anchor="middle" font-size="10" fill="#78350f">Medication is due NOW</text>
  <text x="120" y="108" text-anchor="middle" font-size="10" fill="#78350f">↓</text>
  <text x="120" y="124" text-anchor="middle" font-size="10" fill="#78350f">Play reminder chirp</text>
  <text x="120" y="140" text-anchor="middle" font-size="10" fill="#78350f">Animate gesture</text>
  <text x="120" y="156" text-anchor="middle" font-size="10" fill="#78350f">speak("Time for your</text>
  <text x="120" y="172" text-anchor="middle" font-size="10" fill="#78350f">  Lisinopril!")</text>
  <text x="120" y="192" text-anchor="middle" font-size="9" fill="#a16207">Repeats with escalation ↻</text>

  <!-- Arrow 1→2 -->
  <line x1="220" y1="115" x2="278" y2="115" stroke="#6366f1" stroke-width="2" marker-end="url(#arr)"/>
  <text x="249" y="108" text-anchor="middle" font-size="9" fill="#6366f1">patient</text>
  <text x="249" y="120" text-anchor="middle" font-size="9" fill="#6366f1">shows bottle</text>

  <!-- Step 2: VERIFY -->
  <rect x="280" y="30" width="200" height="170" rx="12" fill="#dbeafe" stroke="#3b82f6" stroke-width="2"/>
  <text x="380" y="58" text-anchor="middle" font-size="14" font-weight="700" fill="#1e3a8a">2. VERIFY</text>
  <line x1="300" y1="68" x2="460" y2="68" stroke="#93c5fd" stroke-width="1"/>
  <text x="380" y="88" text-anchor="middle" font-size="10" fill="#1e3a8a">Patient holds up bottle</text>
  <text x="380" y="108" text-anchor="middle" font-size="10" fill="#1e3a8a">↓</text>
  <text x="380" y="124" text-anchor="middle" font-size="10" fill="#1e3a8a">VLM reads the label</text>
  <text x="380" y="140" text-anchor="middle" font-size="10" fill="#1e3a8a">Compares to due med</text>
  <text x="380" y="160" text-anchor="middle" font-size="10" fill="#166534">✓ Right → nod_yes()</text>
  <text x="380" y="178" text-anchor="middle" font-size="10" fill="#991b1b">✗ Wrong → shake_no()</text>
  <text x="380" y="192" text-anchor="middle" font-size="9" fill="#1d4ed8">NEVER accepts wrong meds</text>

  <!-- Arrow 2→3 -->
  <line x1="480" y1="115" x2="538" y2="115" stroke="#6366f1" stroke-width="2" marker-end="url(#arr)"/>
  <text x="509" y="108" text-anchor="middle" font-size="9" fill="#6366f1">patient</text>
  <text x="509" y="120" text-anchor="middle" font-size="9" fill="#6366f1">thumbs up 👍</text>

  <!-- Step 3: CONFIRM -->
  <rect x="540" y="30" width="210" height="170" rx="12" fill="#dcfce7" stroke="#22c55e" stroke-width="2"/>
  <text x="645" y="58" text-anchor="middle" font-size="14" font-weight="700" fill="#166534">3. CONFIRM</text>
  <line x1="560" y1="68" x2="730" y2="68" stroke="#86efac" stroke-width="1"/>
  <text x="645" y="88" text-anchor="middle" font-size="10" fill="#166534">Patient gives thumbs up</text>
  <text x="645" y="108" text-anchor="middle" font-size="10" fill="#166534">↓</text>
  <text x="645" y="124" text-anchor="middle" font-size="10" fill="#166534">mark_medication_taken()</text>
  <text x="645" y="140" text-anchor="middle" font-size="10" fill="#166534">Celebration sound</text>
  <text x="645" y="156" text-anchor="middle" font-size="10" fill="#166534">Happy wiggle dance</text>
  <text x="645" y="172" text-anchor="middle" font-size="10" fill="#166534">speak("Boom! You're</text>
  <text x="645" y="188" text-anchor="middle" font-size="10" fill="#166534">  basically a superhero.")</text>

  <!-- Bottom note -->
  <text x="390" y="228" text-anchor="middle" font-size="10" font-style="italic" fill="#94a3b8">Safety first: the robot will NEVER accept the wrong medication, even if the patient insists.</text>
</svg>
```

### Person Presence Detection

The vision loop tracks whether someone is in front of the camera using a simple **keyword-based state machine**, no separate face detection model needed.

After each VLM response, the text output is scanned for keywords:

- **Person present**: "person", "someone", "patient", "face", "thumbs", "holding", etc.
- **No one present**: "no one", "nobody", "empty", "alone", "waiting"

State transitions:
- **No one → Person detected**: Inject "🆕 NEW PERSON" context, VLM greets once
- **Person present**: Inject "👤 PATIENT PRESENT" context, no re-greeting
- **Person → No one**: Reset greeting state, ready for next visitor

### VLM Client Architecture

The VLM integration uses an **abstract base class** pattern so backends are swappable:

```
BaseVLMClient (ABC)
├── LMStudioVLMClient  - Tools described in system prompt, parsed from text via regex
└── OpenAIVLMClient    - Native structured tool calls via tools= API parameter
```

**Why two clients?** LM Studio's tool call parser silently drops tool calls for certain models (including Nemotron VL). The LM Studio client works around this by embedding tool descriptions in the system prompt and using regex to extract calls like `nod_yes()` or `speak({"message": "Hello!"})` from the model's text output.

The OpenAI client works with any server that properly implements the OpenAI tools API (vLLM, Ollama, OpenAI itself).

Both clients share:
- **Frame encoding**: JPEG → base64 data URI (85% quality)
- **Rolling history**: Last 100 action/observation entries to prevent repetition
- **Context injection**: `inject_context()` prepends situational info to the next prompt
- **Async support**: `step_async()` / `step_collect()` for overlapping network latency with action execution

### File Map

| File | Purpose |
|---|---|
| `main.py` | Entry point, vision loop, camera init, Reachy connection, context injection, person state machine |
| `vlm_client.py` | Base client + tool definitions (6 tools) + `execute_tool_calls()` gesture choreography |
| `vlm_client_lmstudio.py` | LM Studio backend, regex-based text tool call parsing |
| `vlm_client_openai.py` | Standard OpenAI-compatible backend |
| `medication_reminder.py` | Google Sheets schedule fetcher, due-med checker, taken-log persistence |
| `minimax_tts.py` | Direct MiniMax HTTP TTS → Reachy speaker, non-blocking daemon thread |
| `sounds.py` | Synthesized sound effects (chirps, celebration), pure numpy, no audio files |
| `macbook_camera.py` | MacBook FaceTime camera fallback for development |
| `system_prompt.md` | Robot persona, behavior rules, action examples |
| `medication_taken.json` | Daily log of medications taken (auto-generated, gitignored) |

### Tech Stack

| Component | Technology |
|---|---|
| **Robot** | [Reachy Mini](https://github.com/pollen-robotics/reachy-mini), desktop robot with head servos, antennas, speaker |
| **VLM** | [NVIDIA Nemotron Nano VL 12B V2 FP8](https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8), 13B param vision-language model on NVIDIA L40S (48 GiB) via [Brev](https://brev.dev/) |
| **TTS** | [MiniMax T2A v2](https://www.minimaxi.com/), `speech-2.6-turbo` model, `English_Upbeat_Woman` voice |
| **Schedule** | Google Sheets via gviz JSON API |
| **Language** | Python 3.11–3.12, managed with [uv](https://docs.astral.sh/uv/) |
| **Vision** | OpenCV (BGR → JPEG → base64) |
| **Audio** | numpy-synthesized sounds at 16kHz, pushed via Reachy's PCM speaker API |
| **Serving** | vLLM on NVIDIA L40S (48 GiB) via [Brev](https://brev.dev/), exposed through Cloudflare tunnel |
