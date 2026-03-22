import os
import base64
import requests
import json
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("AGORA_APP_ID")
REST_KEY = os.getenv("AGORA_RESTFUL_KEY")
REST_SECRET = os.getenv("AGORA_RESTFUL_SECRET")

AGORA_TOKEN = os.getenv("AGORA_CHANNEL_TOKEN", "")

credentials = f"{REST_KEY}:{REST_SECRET}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

url = f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{APP_ID}/join"

headers = {
    "Authorization": f"Basic {encoded_credentials}",
    "Content-Type": "application/json"
}

system_prompt = "You are the friendly voice of a Reachy Mini pharmacist robot. Keep answers concise and conversational."

payload = {
    "name": "reachy_conversation",
    "preset": "openai_gpt_4_1_mini,minimax_speech_2_6_turbo",
    "properties": {
        "channel": "reachy_conversation",
        "token": AGORA_TOKEN,
        "agent_rtc_uid": "1000",
        "remote_rtc_uids": [
            "12345"
        ],
        "enable_string_uid": False,
        "idle_timeout": 120,
        "asr": {
            "language": "en-US"
        },
        "llm": {
            "system_messages": [
                {
                    "role": "system",
                    "content": system_prompt
                }
            ],
            "greeting_message": "Hi, how can I help you today?"
        },
        "tts": {
            "vendor": "minimax",
            "params": {
                "voice_setting": {
                    "voice_id": "English_Strong-WilledBoy"
                },
                "audio_setting": {
                    "sample_rate": 44100
                }
            }
        }
    }
}

try:
    print("Starting Reachy AI Agent...")
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    response.raise_for_status() 
    
    data = response.json()
    print("✅ Agent successfully started!")
    print(f"Agent ID: {data.get('agent_id')}")
    print(f"Status: {data.get('status')}")
    
except requests.exceptions.RequestException as e:
    print("❌ Failed to start the agent.")
    print(f"Error: {e}")
    if hasattr(e, 'response') and e.response is not None:
        print(f"Details: {e.response.text}")
