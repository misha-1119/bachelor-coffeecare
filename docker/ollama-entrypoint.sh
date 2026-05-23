#!/bin/sh
set -e

MODEL="${LAPA_MODEL:-hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF}"

ollama serve &
OLLAMA_PID=$!

echo "[ollama] waiting for server..."
until bash -c '</dev/tcp/localhost/11434' 2>/dev/null; do
    sleep 2
done
echo "[ollama] server ready"

if ! ollama list 2>/dev/null | grep -qF "$MODEL"; then
    echo "[ollama] pulling $MODEL (first run — may take several minutes)..."
    ollama pull "$MODEL"
    echo "[ollama] pull complete"
else
    echo "[ollama] model already cached"
fi

wait $OLLAMA_PID
