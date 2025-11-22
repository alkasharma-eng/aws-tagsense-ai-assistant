# Multi-stage Dockerfile for AWS TagSense
# Production-grade containerization with security hardening

# ============================================================================
# Stage 1: Builder - Install dependencies
# ============================================================================
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# ============================================================================
# Stage 2: Runtime - Minimal production image
# ============================================================================
FROM python:3.11-slim

# Metadata
LABEL maintainer="AWS TagSense Team"
LABEL version="2.0.0"
LABEL description="Enterprise Graph-Based Metadata Platform for AWS"

# Create non-root user for security
RUN groupadd -r tagsense && useradd -r -g tagsense tagsense

# Set working directory
WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /root/.local /home/tagsense/.local

# Copy application code
COPY --chown=tagsense:tagsense . .

# Create directories for data persistence
RUN mkdir -p /app/data /app/logs && \
    chown -R tagsense:tagsense /app/data /app/logs

# Set Python path
ENV PATH=/home/tagsense/.local/bin:$PATH
ENV PYTHONPATH=/app:$PYTHONPATH
ENV PYTHONUNBUFFERED=1

# Expose Streamlit default port
EXPOSE 8501

# Expose FastAPI port (for REST API)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Switch to non-root user
USER tagsense

# Default command: Run Streamlit app
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=true"]
