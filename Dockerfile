# ==============================================================================
# Reproducible Slither Static Analysis Environment
# ==============================================================================
# Pinned versions:
# - Python: 3.11-slim
# - Slither: 0.10.4
# - solc-select: 1.0.4
# - Pre-installed solc: 0.8.20, 0.8.24 (default: 0.8.20)
#
# IMPORTANT:
# Analysis containers MUST run with `--network none`.
# All compilers and dependencies are baked in at build time.
# ==============================================================================

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOLC_SELECT_DIR="/home/slitheruser/.solc-select" \
    PATH="/home/slitheruser/.local/bin:/home/slitheruser/.solc-select/artifacts:$PATH"

# Install minimal OS dependencies required by solc binaries and git metadata
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root unprivileged user (UID 1000)
RUN useradd -m -u 1000 -s /bin/bash slitheruser

USER slitheruser
WORKDIR /home/slitheruser

# Install pinned solc-select and slither-analyzer
RUN pip install --no-cache-dir --user \
    solc-select==1.0.4 \
    slither-analyzer==0.10.4

# Pre-install deterministic Solidity compiler versions during build
RUN /home/slitheruser/.local/bin/solc-select install 0.8.20 && \
    /home/slitheruser/.local/bin/solc-select install 0.8.24 && \
    /home/slitheruser/.local/bin/solc-select use 0.8.20

# Create standard mount points with appropriate permissions
USER root
RUN mkdir -p /repo /output && \
    chown slitheruser:slitheruser /output && \
    chmod 755 /repo && \
    chmod 777 /output

USER slitheruser
WORKDIR /repo

# Default empty entrypoint so commands (slither, solc, etc.) can be passed directly
ENTRYPOINT []
CMD ["slither", "--help"]
