FROM python:3.11-slim
WORKDIR /opt/audiobook
COPY coordinator /opt/audiobook/coordinator
COPY config/coordinator.yaml /opt/audiobook/config/coordinator.yaml
RUN pip install --no-cache-dir -r /opt/audiobook/coordinator/requirements.txt
ENV PYTHONPATH=/opt/audiobook
ENV COORDINATOR_CONFIG=/opt/audiobook/config/coordinator.yaml
EXPOSE 8000
CMD ["python", "-m", "coordinator.main"]
