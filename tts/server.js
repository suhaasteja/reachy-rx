const express = require("express");
const cors = require("cors");
const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "../.env") });

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(__dirname));

const APP_ID = process.env.AGORA_APP_ID;
const REST_KEY = process.env.AGORA_RESTFUL_KEY;
const REST_SECRET = process.env.AGORA_RESTFUL_SECRET;
const CHANNEL_TOKEN = process.env.AGORA_CHANNEL_TOKEN || "";

const AGORA_BASE = `https://api.agora.io/api/conversational-ai-agent/v2/projects/${APP_ID}`;
const AUTH_HEADER =
  "Basic " + Buffer.from(`${REST_KEY}:${REST_SECRET}`).toString("base64");

// Keep track of active agent so we can stop it
let activeAgentId = null;

// POST /speak  — start an agent that speaks the given text
app.post("/speak", async (req, res) => {
  const { text } = req.body;
  if (!text) return res.status(400).json({ error: "text is required" });

  // Stop any existing agent first
  if (activeAgentId) {
    try {
      await stopAgent(activeAgentId);
    } catch (_) {}
    activeAgentId = null;
  }

  const agentName = `tts_${Date.now()}`;
  const payload = {
    name: agentName,
    preset: "openai_gpt_4_1_mini,minimax_speech_2_6_turbo",
    properties: {
      channel: "tts_channel",
      token: CHANNEL_TOKEN,
      agent_rtc_uid: "1000",
      remote_rtc_uids: ["12345"],
      enable_string_uid: false,
      idle_timeout: 30,
      asr: {
        language: "en-US",
      },
      llm: {
        system_messages: [
          {
            role: "system",
            content:
              "You are a text-to-speech service. When the user sends you text, repeat it back EXACTLY word for word. Do not add anything. Do not explain. Just say exactly what was sent.",
          },
        ],
        greeting_message: text,
        max_history: 1,
      },
      tts: {
        vendor: "minimax",
        params: {
          voice_setting: {
            voice_id: "English_Strong-WilledBoy",
          },
          audio_setting: {
            sample_rate: 44100,
          },
        },
      },
    },
  };

  try {
    const response = await fetch(`${AGORA_BASE}/join`, {
      method: "POST",
      headers: {
        Authorization: AUTH_HEADER,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (!response.ok) {
      return res.status(response.status).json(data);
    }

    activeAgentId = data.agent_id;
    res.json({ agent_id: data.agent_id, status: data.status });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /stop  — stop the active agent
app.post("/stop", async (req, res) => {
  const agentId = req.body.agent_id || activeAgentId;
  if (!agentId) return res.status(400).json({ error: "no active agent" });

  try {
    await stopAgent(agentId);
    activeAgentId = null;
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

async function stopAgent(agentId) {
  const response = await fetch(`${AGORA_BASE}/agents/${agentId}/leave`, {
    method: "POST",
    headers: { Authorization: AUTH_HEADER },
  });
  return response.json();
}

// GET /config — expose non-secret config to the frontend
app.get("/config", (_req, res) => {
  res.json({ app_id: APP_ID });
});

// GET / serves the HTML
app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "index.html"));
});

const PORT = 3456;
app.listen(PORT, () => {
  console.log(`Agora TTS server running at http://localhost:${PORT}`);
  console.log(`App ID: ${APP_ID}`);
});
