# Dockerfile for machine-logic-service (FastAPI Backend) - Optimized

# 1. Build Stage
FROM python:3.10-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="${PYTHONPATH}:/app"

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# 2. Production Stage
FROM python:3.10-slim-bookworm AS runner

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="${PYTHONPATH}:/app"
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Create non-root user
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --gid 1001 --shell /bin/false appuser

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application files
COPY src/ ./src/
COPY entrypoint.sh ./
RUN dos2unix entrypoint.sh && chmod +x entrypoint.sh

# Change ownership
RUN chown -R appuser:appgroup /app /home/appuser/.local

USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"] 