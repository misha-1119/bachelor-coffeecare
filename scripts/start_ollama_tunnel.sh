#!/bin/bash
# Start Ollama (Docker) + ngrok tunnel for Railway LLM access

set -e

echo "Starting Ollama..."
docker compose up -d ollama
echo "Waiting for Ollama to be ready..."
until curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do sleep 2; done
echo "Ollama ready."

echo "Starting ngrok tunnel..."
pkill -f "ngrok http 11434" 2>/dev/null || true
ngrok http 11434 --log=stdout > /tmp/ngrok_ollama.log 2>&1 &
sleep 5

URL=$(curl -s http://localhost:4040/api/tunnels | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])")

echo ""
echo "========================================"
echo "  Ollama tunnel active"
echo "========================================"
echo "  NGROK URL : $URL"
echo ""
echo "  Railway env vars to set:"
echo "  OLLAMA_URL=$URL/api/generate"
echo "  (remove DISABLE_LLAMA if set)"
echo "========================================"
echo ""
echo "Keep this terminal open. URL changes on restart."
echo "Press Ctrl+C to stop."

wait
