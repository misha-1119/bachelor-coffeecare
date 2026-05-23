FROM python:3.12-slim

WORKDIR /app

# CPU-only torch first — avoids pulling 2GB CUDA wheels
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/knowledge_base.json data/knowledge_base.json
COPY main.py bot.py ./

CMD ["python", "main.py", "bot"]
