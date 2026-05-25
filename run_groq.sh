#!/bin/bash
# Run CaffeBot locally using Groq API (no Docker, no Ollama needed).
# Requires: Python 3.12+, pip install -r requirements.txt, .env with GROQ_API_KEY.

set -e

REQUIRED_VARS=(TELEGRAM_BOT_TOKEN CONVEX_URL QDRANT_URL QDRANT_API_KEY GROQ_API_KEY)

if [ ! -f .env ]; then
    echo ""
    echo "ERROR: .env not found."
    echo "  cp .env.example .env"
    echo "  Fill in all required credentials including GROQ_API_KEY."
    echo ""
    exit 1
fi

set -a; source .env; set +a

missing=()
for v in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!v}" ]; then
        missing+=("$v")
    fi
done
if [ "${#missing[@]}" -ne 0 ]; then
    echo ""
    echo "ERROR: Missing required vars in .env:"
    for v in "${missing[@]}"; do echo "  $v"; done
    echo ""
    exit 1
fi

export DISABLE_LLAMA=1

echo ""
echo "========================================"
echo "  CaffeBot — Groq mode (local run)"
echo "  LLM : llama-3.3-70b-versatile @ Groq"
echo "  No Ollama / Docker required"
echo "========================================"
echo ""

python main.py bot
