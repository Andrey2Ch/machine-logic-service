# Dockerfile for machine-logic-service (FastAPI Backend)

# 1. Base Image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH "${PYTHONPATH}:/app"

# Set the working directory
WORKDIR /app

# Install dependencies
# Copy requirements file first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the application source code
# This copies the contents of the 'src' directory into a 'src' subdir in WORKDIR
COPY src/ ./src/

# The command to run the application
# Было:
# CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "src.main:app", "-b", "0.0.0.0:8000"]

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"] 