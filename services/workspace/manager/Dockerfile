FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /manager
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app

RUN useradd --create-home --uid 10003 manager \
    && chown -R manager:manager /manager
USER manager

# The manager is intentionally unprivileged and has no Docker socket. All
# Docker lifecycle operations are delegated to the narrow workspace broker.
EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
