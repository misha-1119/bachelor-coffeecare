"""
Entry point.

Usage:
    python main.py demo   # CLI demo (no Telegram)
    python main.py eval   # run evaluation
    python main.py bot    # start Telegram bot
"""

import sys

from dotenv import load_dotenv

load_dotenv()


def run_demo():
    from src.assistant import CoffeeBotAssistant

    bot = CoffeeBotAssistant()
    print("\nCoffeeBot demo. Введіть запит або 'exit'.\n")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q or q.lower() in {"exit", "quit"}:
            break
        result = bot.respond(q)
        print(f"\n[category={result['category']} kb={result['kb_entry_id']} "
              f"conf={result['confidence']:.3f} src={result['source']}]")
        print(result["response"])
        print()


def run_eval():
    from evaluation.evaluate import run_full_evaluation

    run_full_evaluation()


def run_bot():
    from bot import start_bot

    start_bot()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        run_demo()
    elif cmd == "eval":
        run_eval()
    elif cmd == "bot":
        run_bot()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python main.py [demo|eval|bot]")
        sys.exit(1)


if __name__ == "__main__":
    main()
