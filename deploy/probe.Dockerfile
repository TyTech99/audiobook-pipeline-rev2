FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx
COPY supervisor/probe_client.py /app/probe_client.py
ENTRYPOINT ["python", "/app/probe_client.py"]
