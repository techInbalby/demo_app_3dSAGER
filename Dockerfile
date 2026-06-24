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

# Copy requirements files (app + standalone inference pipeline)
COPY requirements.txt .
COPY demo_infrance_pipeline/requirements.txt /tmp/pipeline-requirements.txt

# Install Python dependencies. The pipeline bundle's requirements install LAST
# so its newer numpy/pandas/sklearn/xgboost win over the app's older pins —
# the new pipeline_stages code is built against them. We strip torch /
# torchvision / clip from the pipeline requirements: they're only needed for the
# ViT-based blocking variant which the demo does not use, and add ~2 GB.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    sed -i '/^torch/d; /^torchvision/d; /^clip[[:space:]]*$/d' /tmp/pipeline-requirements.txt && \
    pip install --no-cache-dir -r /tmp/pipeline-requirements.txt

# Copy application code (excluding data - we'll handle it separately)
COPY app.py tasks.py requirements.txt ./
COPY templates/ templates/
COPY static/ static/
COPY deploy/ deploy/

# New live-pipeline packages and the standalone inference bundle.
COPY pipeline/                pipeline/
COPY align_api/              align_api/
COPY data_api/               data_api/
COPY features_api/           features_api/
COPY bkafi_api/              bkafi_api/
COPY building_api/           building_api/
COPY lib/                    lib/
COPY demo_infrance_pipeline/  demo_infrance_pipeline/

# Copy scripts
COPY scripts/ scripts/

# Copy data directory into image
# This avoids file locking issues with cloud storage (OneDrive) mounts
# Data is baked into the image, so updates require rebuild
COPY data/ /app/data/

# Pre-bake CityJSON files: transform coords to WGS84 and extract footprint+height.
# This eliminates client-side proj4 transforms and vertex processing, cutting
# browser parse time from ~30 s per file to under 2 s.
RUN DATA_DIR=/app/data python scripts/prebake_cityjson.py

# Create necessary directories
RUN mkdir -p logs results saved_model_files results_demo/cache && \
    touch logs/.gitkeep

# Expose Flask port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/')" || exit 1

# Run Flask application (configurable via env vars)
#CMD ["python", "app.py"]
CMD ["/bin/sh", "-c", "gunicorn -c deploy/gunicorn.conf.py app:app"]

