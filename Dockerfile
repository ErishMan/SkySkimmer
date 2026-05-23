FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - \
    && ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy dependency files first (layer caching)
COPY pyproject.toml poetry.lock* ./

# Install production dependencies only
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copy application source
COPY . .

# Create directories for logs and local state
RUN mkdir -p logs data

# Non-root user for security
RUN useradd -m -u 1000 skyskimmer && chown -R skyskimmer:skyskimmer /app
USER skyskimmer

# Health check (process-level — no HTTP server)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "-m", "src.main"]
