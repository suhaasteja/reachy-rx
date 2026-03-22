#!/bin/bash
# Start both Agora voice agent and vision loop together

echo "🤖 Starting Reachy RX - Full System"
echo "===================================="
echo ""

# Start Agora voice agent
echo "1️⃣ Starting Agora voice agent..."
python agora_voice_agent.py

if [ $? -eq 0 ]; then
    echo "✅ Voice agent started successfully!"
    echo ""
    
    # Open the voice test HTML in browser
    echo "2️⃣ Opening voice test client in browser..."
    open agora_voice_test.html
    echo ""
    
    echo "3️⃣ Starting vision loop (if VLM available)..."
    echo "   To start vision loop manually, run:"
    echo "   python main.py"
    echo ""
    echo "🎉 Setup complete!"
    echo ""
    echo "Next steps:"
    echo "  - Click 'Join Voice Channel' in the browser"
    echo "  - Allow microphone access"
    echo "  - Speak to test voice interaction"
    echo "  - Run 'python main.py' separately for vision loop"
else
    echo "❌ Failed to start voice agent"
    exit 1
fi
