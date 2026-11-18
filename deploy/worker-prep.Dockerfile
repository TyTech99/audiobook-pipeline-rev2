FROM python:3.11-slim
WORKDIR /opt/audiobook
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY worker /opt/audiobook/worker
COPY config/worker-prep.yaml /opt/audiobook/config/worker-prep.yaml
RUN pip install --no-cache-dir -r /opt/audiobook/worker/prep/requirements.txt
ENV PYTHONPATH=/opt/audiobook
ENV PREP_WORKER_CONFIG=/opt/audiobook/config/worker-prep.yaml
CMD ["python", "-m", "worker.prep.worker"]
