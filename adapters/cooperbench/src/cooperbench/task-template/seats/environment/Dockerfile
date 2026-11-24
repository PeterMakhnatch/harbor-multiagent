FROM {image_name}

# main holds the base commit and runs the message service between the seats
# (docker-compose.yaml). The verifier runs here too. No agent runs in main.
ENTRYPOINT []
CMD ["bash"]

# Ensure language toolchains are in PATH.
# Docker ENV PATH is overwritten by /etc/profile in login shells (bash -l),
# which Daytona DinD uses for exec. Write to /etc/profile.d/ so the PATH
# survives login-shell resets, and keep ENV for non-login contexts.
ENV PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"
RUN mkdir -p /etc/profile.d && \
    printf '%s\n' 'export PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"' \
    > /etc/profile.d/toolchain-path.sh

COPY coop_server.py /opt/cooperbench/coop_server.py

# Harbor infrastructure directories, and the seats' token directories
RUN mkdir -p /logs/verifier /logs/agent /logs/cooperbench /patches /tests \
    /run/cooperbench/tokens/agent1 /run/cooperbench/tokens/agent2

# Git identity for merge operations in verifier
RUN git config --global user.email "eval@cooperbench.local" && \
    git config --global user.name "CooperBench Eval"
