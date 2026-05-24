#!/bin/bash
# Start CaffeBot full local stack via Docker Compose.
# Requires: Docker Desktop running, .env with credentials filled in.

set -e

REQUIRED_VARS=(TELEGRAM_BOT_TOKEN CONVEX_URL QDRANT_URL QDRANT_API_KEY)

# ---- validate .env ----
if [ ! -f .env ]; then
    echo ""
    echo "ERROR: .env not found."
    echo "  cp .env.example .env"
    echo "  Then fill in TELEGRAM_BOT_TOKEN, CONVEX_URL, QDRANT_URL, QDRANT_API_KEY."
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

# ---- check Docker ----
if ! docker info > /dev/null 2>&1; then
    echo ""
    echo "ERROR: Docker is not running. Open Docker Desktop and retry."
    echo ""
    exit 1
fi

echo ""
echo "========================================"
echo "  CaffeBot — local demo stack"
echo "========================================"
echo ""

# ---- build + start ----
echo "[1/3] Building bot image..."
docker compose build bot

echo ""
echo "[2/3] Starting services (qdrant + ollama + bot)..."
echo "      First run: Ollama will pull lapa model (~4 GB, takes a few minutes)."
echo "      Subsequent runs: model already cached, starts in seconds."
echo ""
docker compose up -d

echo "[3/3] Waiting for services to come up..."
sleep 6

echo ""
echo "Status:"
docker compose ps
echo ""
echo "========================================"
echo "  Bot is live. Open Telegram and send /start"
echo ""
echo "  Follow logs : docker compose logs -f bot"
echo "  Stop all    : docker compose down"
echo "========================================"
echo ""

docker compose logs -f bot
