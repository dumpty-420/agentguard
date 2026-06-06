# Dockerfile — Production-grade multi-stage build for AgentGuard.
# Targets GCP Cloud Run / local compose.

# -- Build Stage -------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install system dependencies needed for compiling python packages (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency configuration files
COPY pyproject.toml requirements.txt ./

# Synchronize dependencies into a virtualenv (without the app itself first)
RUN uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv pip install -r requirements.txt fastapi "uvicorn[standard]"

# -- Final Stage -------------------------------------------------------------
FROM python:3.12-slim AS runner

WORKDIR /app

# Create a non-root system user for security hardening
RUN groupadd -g 10001 appgroup \
    && useradd -u 10001 -g appgroup -m -s /bin/bash appuser

# Copy virtualenv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source code and config
COPY agents/ ./agents/
COPY config/ ./config/
COPY core/ ./core/
COPY runtime/ ./runtime/
COPY main.py ./

# Adjust ownership to the non-root user
RUN chown -R appuser:appgroup /app

USER appuser

# Expose FastAPI application port
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Run FastAPI app using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
