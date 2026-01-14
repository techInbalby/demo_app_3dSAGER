# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    FLASK_ENV=production

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code (excluding data - we'll handle it separately)
COPY app.py requirements.txt ./
COPY templates/ templates/
COPY static/ static/

# Copy data directory into image
# This avoids file locking issues with cloud storage (OneDrive) mounts
# Data is baked into the image, so updates require rebuild
COPY data/ /app/data/

# Create necessary directories
RUN mkdir -p logs results saved_model_files && \
    touch logs/.gitkeep

# Expose Flask port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/')" || exit 1

# Run Flask application
CMD ["python", "app.py"]

