FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# lark-cli binary (Linux amd64)
# 替换为实际下载地址或通过 volume 挂载
ARG LARK_CLI_URL="https://github.com/lark-cli/releases/latest/download/lark-cli-linux-amd64"
RUN curl -fsSL -o /usr/local/bin/lark-cli ${LARK_CLI_URL} && \
    chmod +x /usr/local/bin/lark-cli || \
    echo "lark-cli download skipped — mount or build with --build-arg LARK_CLI_URL"

# Application
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY config.example.yaml ./

# lark-cli.cmd wrapper for Linux compatibility
RUN echo '#!/bin/sh' > /usr/local/bin/lark-cli.cmd && \
    echo 'exec lark-cli "$@"' >> /usr/local/bin/lark-cli.cmd && \
    chmod +x /usr/local/bin/lark-cli.cmd

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "scripts/unified_listener.py"]
CMD ["--mode", "auto"]
