FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSPACE_ROOT=/workspaces

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runtime
COPY pyproject.toml ./
RUN pip install --no-cache-dir '.[dev]' \
    && pip install --no-cache-dir \
        'sqlalchemy>=2.0,<3' \
        'alembic>=1.13,<2' \
        'psycopg[binary]>=3.2,<4' \
        'pydantic[email]>=2.9,<3'

RUN mkdir -p /opt/inzozi-node \
    && cd /opt/inzozi-node \
    && npm init -y >/dev/null 2>&1 \
    && npm install --save-exact --no-audit --no-fund \
        react@18.3.1 react-dom@18.3.1 \
        @vitejs/plugin-react@4.3.4 vite@6.0.7 typescript@5.7.3 \
        vitest@2.1.8 jsdom@25.0.1 \
        @testing-library/react@16.1.0 @testing-library/jest-dom@6.6.3 \
        @testing-library/user-event@14.5.2 \
        @types/react@18.3.18 @types/react-dom@18.3.5 \
        @tiptap/core@3.31.4 @tiptap/react@3.31.4 @tiptap/pm@3.31.4 \
        @tiptap/starter-kit@3.31.4 @tiptap/extension-link@3.31.4 \
        dexie@4.4.6 fake-indexeddb@6.2.5 uuid@14.0.2 \
    && chmod -R a+rX /opt/inzozi-node

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
