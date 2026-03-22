# Reachy Voice Testing Guide

## Quick Start

### Option 1: Automated Setup (Recommended)
```bash
./start_reachy_full.sh
```

This will:
1. Start the Agora voice agent
2. Open the voice test client in your browser
3. Show instructions for the vision loop

### Option 2: Manual Setup

**Step 1: Start the voice agent**
```bash
python agora_voice_agent.py
```

You should see:
```
Starting Reachy AI Agent...
✅ Agent successfully started!
Agent ID: A42AT29KE35HV56DH68ME44FW37FJ32W
Status: RUNNING
```

**Step 2: Open the test client**
```bash
open agora_voice_test.html
```

**Step 3: Test voice interaction**
1. Click "Join Voice Channel" in the browser
2. Allow microphone access when prompted
3. Speak to Reachy - the agent will respond with voice
4. Check the console logs for connection status

## Testing Voice Interaction

### What to Say
Try these test phrases:
- "Hello, how are you?"
- "What medications do you know about?"
- "Can you help me with my prescription?"
- "Tell me about drug interactions"

### Expected Behavior
- Agent should respond within 1-2 seconds
- Voice should be the "English_Strong-WilledBoy" from Minimax TTS
- Responses should be concise and conversational (per system prompt)

### Troubleshooting

**No audio response:**
- Check browser console for errors
- Verify microphone permissions are granted
- Ensure you're not using UID 1000 (agent's UID)

**Connection fails:**
- Verify the agent is running (`python agora_voice_agent.py`)
- Check that Agent ID was returned successfully
- Try refreshing the HTML page

**Agent not understanding:**
- Speak clearly and wait for response
- Check that ASR language is set to "en-US"
- Review logs in the HTML console

## Running with Vision Loop

Once you have a VLM set up, you can run both systems in parallel:

**Terminal 1: Voice Agent**
```bash
python agora_voice_agent.py
```

**Terminal 2: Vision Loop** (when VLM is available)
```bash
python main.py
# or with debug mode:
python main.py --debug
```

**Browser: Voice Test Client**
```bash
open agora_voice_test.html
```

## Architecture

```
┌─────────────────────┐
│  Voice Agent        │
│  (Agora Cloud)      │
│  - ASR (Speech→Text)│
│  - LLM (GPT-4o-mini)│
│  - TTS (Minimax)    │
└──────────┬──────────┘
           │
           │ RTC Channel: "reachy_conversation"
           │
┌──────────┴──────────┐
│  HTML Test Client   │
│  (Your Browser)     │
│  - Microphone input │
│  - Speaker output   │
└─────────────────────┘

┌─────────────────────┐
│  Vision Loop        │  (Runs independently)
│  (main.py)          │
│  - Camera frames    │
│  - VLM analysis     │
│  - Head movements   │
└─────────────────────┘
```

## Current Limitations

1. **No shared context** - Voice and vision systems don't communicate yet
2. **Manual coordination** - You need to start both systems separately
3. **No VLM integration** - Vision loop requires a VLM to be set up

## Future Integration Ideas

- Add webhook to receive voice transcripts in vision loop
- Send medication detections to voice agent for discussion
- Unified context: robot can talk about what it sees
- Voice commands trigger vision actions (e.g., "look at this medication")
