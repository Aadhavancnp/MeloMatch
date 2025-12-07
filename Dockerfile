FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies including FFmpeg for audio processing
# Install, then remove build tools to reduce image size
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Runtime dependencies (kept)
    libpq5 \
    libffi8 \
    libssl3 \
    libjpeg62-turbo \
    zlib1g \
    libsndfile1 \
    ffmpeg \
    curl \
    postgresql-client \
    # Build dependencies (will be removed)
    build-essential \
    libpq-dev \
    gcc \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install uv for faster package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files
COPY requirements.txt pyproject.toml ./

# Install Python dependencies using uv with --system flag
RUN uv pip install --system --no-cache-dir -r requirements.txt

# Remove build tools to save ~200MB
RUN apt-get purge -y --auto-remove \
    build-essential \
    gcc \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/logs /app/staticfiles /app/media

# Collect static files (skip if it fails - not critical for build)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Expose port
EXPOSE 8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Default command (can be overridden in docker-compose)
CMD ["uv", "run", "gunicorn", "MeloMatch.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--threads", "2"]
