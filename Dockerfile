FROM python:3.11-slim

# Avoid writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (build-essential and libpq for PostgreSQL)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user (Section 15.6 & 16)
RUN adduser --disabled-password --gecos "" photobooth \
    && mkdir -p /app/storage /app/app/static /app/app/templates \
    && chown -R photobooth:photobooth /app

# Copy application files
COPY . .
RUN chown -R photobooth:photobooth /app

USER photobooth

EXPOSE 8000

CMD ["python", "run.py"]
