# Dockerfile for machine-logic-service (FastAPI Backend) - Optimized

# 1. Build Stage
FROM python:3.10-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    postgresql-dev \
    python3-dev \
    libffi-dev

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
FROM python:3.10-alpine AS runner

# Install runtime dependencies only
RUN apk add --no-cache \
    postgresql-libs \
    && rm -rf /var/cache/apk/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="${PYTHONPATH}:/app"
ENV PATH="/root/.local/bin:${PATH}"

# Create non-root user
RUN addgroup -g 1001 -S appgroup && \
    adduser -u 1001 -S appuser -G appgroup

WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application files
COPY src/ ./src/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Change ownership
RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"] 