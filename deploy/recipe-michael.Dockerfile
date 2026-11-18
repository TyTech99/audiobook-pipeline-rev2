# Michael Chatterbox voice recipe image.
#
# Base: python:3.11-slim with CPU torch (pip default). To build a GPU image,
# swap the base for nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 and install
# torch with the CUDA index, e.g.:
#   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/recipe
COPY recipe/michael-chatterbox /opt/recipe/michael-chatterbox

RUN pip install --no-cache-dir -r /opt/recipe/michael-chatterbox/requirements.txt

ENV RECIPE_DIR=/opt/recipe/michael-chatterbox
ENTRYPOINT ["python", "/opt/recipe/michael-chatterbox/run.py"]
