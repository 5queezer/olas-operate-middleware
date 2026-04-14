FROM python:3.11-slim

# System deps needed by open-autonomy and the middleware
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    docker.io \
    docker-compose \
    curl \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install tendermint v0.34.19 (required by open-autonomy for host deployment)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then TM_ARCH="linux_arm64"; \
    else TM_ARCH="linux_amd64"; fi && \
    curl -sL "https://github.com/tendermint/tendermint/releases/download/v0.34.19/tendermint_0.34.19_${TM_ARCH}.tar.gz" \
    | tar xz -C /usr/local/bin tendermint && \
    chmod +x /usr/local/bin/tendermint

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --without development

# Data directory — mount a persistent volume here
ENV OPERATE_DATA=/app/.operate
ENV DISABLE_PARENT_WATCHDOG=1
VOLUME /app/.operate

EXPOSE 8000

# Bind to 0.0.0.0 so traffic can reach the container
CMD ["operate", "daemon", "--host=0.0.0.0", "--port=8000"]
