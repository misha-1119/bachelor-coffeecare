# Bot Commands & Usage

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Greet user, show keyboard, reset state |
| `/help` | Usage examples |
| `/model` | Set user's machine brand/model (e.g. "DeLonghi Magnifica S") |
| `/mode` | Toggle NLP mode ↔ rule-based mode |
| `/reset` | Clear all user state, restart |
| `/eval` | (dev only) Run evaluation suite, print F1/precision/recall |

## Keyboard Buttons

| Button | Action |
|--------|--------|
| ☕ Моя машина | Prompt user to enter their machine model |
| ⚠️ Коди помилок | Show common error codes overview |
| 💡 Поради | Show general maintenance tips |
| ❓ Допомога | Show usage hints |
| 🔄 Скинути | Reset conversation |

## Conversation Flow

```
User: /start
Bot: Welcome + keyboard shown

User: sets machine model via /model or ☕ button
Bot: Confirms model, stores for session

User: describes problem ("машина показує E03")
Bot:
  1. Triage check (greeting? safety? followup?)
  2. Classifier finds best KB entry (liberta-large)
  3. Lapa LLM rephrases into short Ukrainian reply
  4. Bot sends reply + keyboard

User: "не спрацювало" (didn't work)
Bot: marks last answer as tried, asks for more details

User: "детальніше" (more detail)
Bot: sends full KB answer for last entry

User: "допомогло" (worked!)
Bot: positive acknowledgement
```

## User Modes

- **NLP mode** (default): liberta-large + Lapa LLM pipeline
- **Rule-based mode**: keyword scoring only, no AI — used for evaluation baseline

Toggle with `/mode`.

## Running the Bot

```bash
# Start Ollama first
ollama serve

# Pull Lapa model (first time only)
ollama pull hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF

# Start bot
python3 bot.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `CONVEX_URL` | Yes | Convex deployment URL |
| `LLAMA_MODEL` | No | Ollama model name (default: `hf.co/lapa-llm/lapa-v0.1.2-instruct-GGUF`) |
| `OLLAMA_URL` | No | Ollama endpoint (default: `http://localhost:11434/api/generate`) |
| `DISABLE_LLAMA` | No | Set to `1` to force KB-direct fallback (skip Ollama) |
