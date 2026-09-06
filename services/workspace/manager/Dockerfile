FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /manager
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app

# This is a trusted control-plane container. It intentionally runs as root only
# so it can access the host Docker socket. AI/workspace containers never receive
# that socket and run separately as the unprivileged workspace user.
EXPOSE 8200
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
