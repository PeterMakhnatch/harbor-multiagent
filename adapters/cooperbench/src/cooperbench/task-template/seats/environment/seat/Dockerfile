FROM {image_name}

# A seat: one CooperBench agent's container. Harbor installs and runs the
# seat's agent here with docker compose exec, so the container only stays up.
ENTRYPOINT []
CMD ["sleep", "infinity"]

# Ensure language toolchains are in PATH (see main's Dockerfile)
ENV PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"
RUN mkdir -p /etc/profile.d && \
    printf '%s\n' 'export PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"' \
    > /etc/profile.d/toolchain-path.sh

# Messaging commands: coop-send, coop-broadcast, coop-recv, coop-peek, coop-agents
COPY coop_msg.py /opt/cooperbench/coop_msg.py
RUN for sub in send broadcast recv peek agents; do \
        printf '#!/bin/sh\nexec python3 /opt/cooperbench/coop_msg.py %s "$@"\n' "$sub" \
            > "/usr/local/bin/coop-$sub" && chmod +x "/usr/local/bin/coop-$sub"; \
    done

# Agents that commit need a git identity
RUN git config --global user.email "agent@cooperbench.local" && \
    git config --global user.name "CooperBench Agent"
