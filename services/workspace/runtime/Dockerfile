FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSPACE_ROOT=/workspaces

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runtime
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app

RUN useradd --create-home --uid 10002 workspace \
    && mkdir -p /workspaces \
    && chown -R workspace:workspace /workspaces /runtime
USER workspace

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
