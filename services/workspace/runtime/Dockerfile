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

# Docker COPY preserves build-context modes. Normalize the runtime
# application tree so helper readability cannot depend on the host
# checkout umask or stale working-tree permissions.
RUN find /runtime/app -type d -exec chmod 0755 {} + \
    && find /runtime/app -type f -exec chmod 0644 {} + \
    && chmod 0555 /runtime/app/git_askpass.sh

RUN useradd --create-home --uid 10002 workspace \
    && mkdir -p /workspaces \
    && chown -R workspace:workspace /workspaces /runtime
USER workspace

EXPOSE 8100
ENTRYPOINT ["python", "-m", "app.entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
