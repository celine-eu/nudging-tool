FROM python:3.11-slim
LABEL authors="francesco"


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install OS deps (build tools not strictly required for this set, keep lean)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH=".venv/bin:/root/.local/bin:${PATH}"

# Copy dependency manifests first for better caching
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install deps (no dev deps declared; adjust if you add optional groups)
RUN uv sync --no-editable

# Copy application code
COPY config ./config
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8016
CMD [".venv/bin/hypercorn", "celine.nudging.main:app", "--bind", "0.0.0.0:8016"]
