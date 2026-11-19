FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

RUN command -v bash \
    && groupadd --gid 10001 player \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash player

ENV HOME=/home/player
WORKDIR /home/player
USER player
