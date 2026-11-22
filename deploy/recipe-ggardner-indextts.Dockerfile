# Grover Gardner voice recipe image on IndexTTS-2.5 (Bilibili).
#
# Upstream index-tts pinned at d9e41aac. torch 2.7.0+cu128: upstream's
# own requirements may allow older torch, but the RTX 50-series
# (Blackwell, sm_120) needs CUDA 12.8 kernels — same lesson as the
# CosyVoice image (#88). Python 3.11 per upstream requires-python.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/indextts
RUN git clone https://github.com/index-tts/index-tts.git src \
    && cd src \
    && git checkout d9e41aac89fd00b3d71497fddb287b7f24613712

# typing-extensions first, from PyPI (the cu128 index serves a wheel
# with inconsistent metadata — #88's lesson).
RUN pip install --no-cache-dir typing-extensions \
    && pip install --no-cache-dir torch==2.7.0 torchaudio==2.7.0 \
        --index-url https://download.pytorch.org/whl/cu128

RUN pip install --no-cache-dir ./src

ENV PYTHONPATH=/opt/recipe

COPY recipe/ggardner-indextts /opt/recipe/ggardner-indextts
COPY recipe/common /opt/recipe/common

ENV RECIPE_DIR=/opt/recipe/ggardner-indextts
ENTRYPOINT ["python", "/opt/recipe/ggardner-indextts/run.py"]
