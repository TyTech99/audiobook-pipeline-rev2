FROM python:3.11-slim
WORKDIR /opt/audiobook
COPY worker /opt/audiobook/worker
COPY config/worker-tts.yaml /opt/audiobook/config/worker-tts.yaml
RUN pip install --no-cache-dir -r /opt/audiobook/worker/tts/requirements.txt
ENV PYTHONPATH=/opt/audiobook
ENV TTS_WORKER_CONFIG=/opt/audiobook/config/worker-tts.yaml
# docker.sock is mounted at runtime so the TTS worker can launch recipe containers.
CMD ["python", "-m", "worker.tts.worker"]
