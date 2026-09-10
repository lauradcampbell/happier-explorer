# Northflank builds this image from the repo and runs it as a combined service.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY chatbot_cloud.py ./
# Transcript data: chunks.json, or the chunks_part1/2.json split used for deploys.
COPY chunks*.json ./

# Northflank auto-detects the exposed port; the app reads PORT at startup.
ENV PORT=8080
EXPOSE 8080

# -u keeps print() output unbuffered so it shows up in Northflank's log stream.
CMD ["python", "-u", "chatbot_cloud.py"]
