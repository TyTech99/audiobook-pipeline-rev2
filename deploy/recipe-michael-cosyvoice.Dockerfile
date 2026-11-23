# Michael CosyVoice 3 voice recipe image.
#
# Upstream QwenAudio/CosyVoice pinned at 074ca6dc (with its Matcha-TTS
# submodule). torch 2.7.0+cu128: upstream's own requirements pin
# torch==2.3.1+cu121, which has no kernels for RTX 50-series (Blackwell,
# sm_120) — the 5060 Ti needs the CUDA 12.8 build.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cosyvoice
RUN git clone --recursive https://github.com/QwenAudio/CosyVoice.git src \
    && cd src \
    && git checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc \
    && git submodule update --init --recursive

# typing-extensions first, from PyPI: the pytorch cu128 index serves a
# typing-extensions wheel with inconsistent metadata (Name: typing_extensions),
# which makes pip discard it and try the sdist, whose flit_core build dep is
# absent from that index. Installing the valid PyPI wheel first satisfies
# torch's requirement so the cu128-only step never re-resolves it.
RUN pip install --no-cache-dir typing-extensions \
    && pip install --no-cache-dir torch==2.7.0 torchaudio==2.7.0 \
        --index-url https://download.pytorch.org/whl/cu128

COPY recipe/michael-cosyvoice/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONPATH=/opt/cosyvoice/src:/opt/cosyvoice/src/third_party/Matcha-TTS:/opt/recipe

COPY recipe/michael-cosyvoice /opt/recipe/michael-cosyvoice
COPY recipe/common /opt/recipe/common

ENV RECIPE_DIR=/opt/recipe/michael-cosyvoice
ENTRYPOINT ["python", "/opt/recipe/michael-cosyvoice/run.py"]
