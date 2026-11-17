FROM python:3.11-slim
WORKDIR /opt/audiobook
COPY supervisor /opt/audiobook/supervisor
COPY config/supervisor.yaml /opt/audiobook/config/supervisor.yaml
RUN pip install --no-cache-dir -r /opt/audiobook/supervisor/requirements.txt
ENV PYTHONPATH=/opt/audiobook
ENV SUPERVISOR_CONFIG=/opt/audiobook/config/supervisor.yaml
CMD ["python", "-m", "supervisor.supervisor"]
