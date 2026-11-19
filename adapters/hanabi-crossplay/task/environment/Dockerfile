FROM python:3.12-slim

RUN pip install --no-cache-dir --only-binary=:all: --no-deps "open_spiel==2.0.2" \
    && pip install --no-cache-dir --only-binary=:all: "numpy>=1.21.5" "absl-py>=0.10.0"

WORKDIR /world
COPY engine.py world.py config.json /world/
