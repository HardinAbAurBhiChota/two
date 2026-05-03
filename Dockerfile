FROM python:3.11-slim

RUN apt-get update && apt-get install -y tor && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["bash", "-c", "tor & sleep 5 && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
