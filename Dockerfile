FROM python:3.13-slim

# Install git and gh CLI dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && apt-get install -y gh && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create non-root user (Claude Code CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash clayde

ENV PATH="/opt/clayde/.venv/bin:$PATH"

WORKDIR /opt/clayde

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY src/ src/
COPY CLAUDE.md ./
RUN uv sync --frozen --no-dev

# Create data directories and set ownership
RUN mkdir -p /data/repos /data/logs && chown -R clayde:clayde /data

# Switch to non-root user and configure git
USER clayde
RUN git config --global credential.helper '!gh auth git-credential' && \
    git config --global user.name "Clayde" && \
    git config --global user.email "clayde@vtettenborn.net"

ENTRYPOINT ["/opt/clayde/.venv/bin/clayde"]
