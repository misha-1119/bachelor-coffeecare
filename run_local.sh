#!/bin/bash
# Launcher — choose mode based on available credentials.
#
#   run_groq.sh   — local Python + Groq API (fast, no Docker for LLM)
#   run_lapa.sh   — Docker Compose + local Ollama/Lapa (offline LLM)

echo ""
echo "Choose how to run CaffeBot:"
echo "  1) Groq mode  : ./run_groq.sh  (requires GROQ_API_KEY in .env, no Docker for LLM)"
echo "  2) Lapa mode  : ./run_lapa.sh  (requires Docker Desktop, downloads ~4 GB Lapa model)"
echo ""
