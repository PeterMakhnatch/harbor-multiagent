FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

COPY kitchen-requirements.txt /tmp/kitchen-requirements.txt
RUN python -m pip --disable-pip-version-check install --no-cache-dir \
        --only-binary=:all: --require-hashes -r /tmp/kitchen-requirements.txt \
    && rm /tmp/kitchen-requirements.txt

RUN revision=739950a079cdaed5a44fcc662efc40244c205d06 \
    && archive=/tmp/overcooked-ai.tar.gz \
    && python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        "https://codeload.github.com/HumanCompatibleAI/overcooked_ai/tar.gz/${revision}" "$archive" \
    && echo "512198f2237590612bdd152bc852026b648744bd9484b7c48c77d699616db471  $archive" \
        | sha256sum --check --strict \
    && mkdir -p /opt/overcooked-ai \
    && root="overcooked_ai-${revision}" \
    && package="$root/src/overcooked_ai_py" \
    && tar -xzf "$archive" -C /opt/overcooked-ai --strip-components=1 \
        "$root/LICENSE" \
        "$package/__init__.py" \
        "$package/static.py" \
        "$package/utils.py" \
        "$package/mdp/__init__.py" \
        "$package/mdp/actions.py" \
        "$package/mdp/overcooked_mdp.py" \
        "$package/data/layouts/cramped_room.layout" \
    && rm "$archive"

ENV PYTHONPATH=/opt/overcooked-ai/src
COPY kitchen_world.py /world/kitchen_world.py
