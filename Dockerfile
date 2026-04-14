FROM python:3.11-slim

# System deps needed by open-autonomy and the middleware
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    docker.io \
    docker-compose \
    curl \
    && rm -rf /var/lib/apt/lists/*

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
