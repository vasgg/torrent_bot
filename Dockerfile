FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set working directory
WORKDIR /app
ENV PYTHONPATH=/app

# Install dependencies
COPY pyproject.toml uv.lock ./
# Install build dependencies for compiling packages
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project --python 3.12

# Copy source code
COPY src ./src


# Create torrents directory (for local runs without a volume)
RUN mkdir -p /mnt/foundation/torrents/incoming/Movies /mnt/foundation/torrents/incoming/Series


# Run the bot
CMD ["uv", "run", "python", "src/main.py"]
