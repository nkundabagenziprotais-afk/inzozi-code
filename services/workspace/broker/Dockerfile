FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /broker
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app

# This broker is the only Inzozi Code service allowed to receive the host Docker
# socket. It exposes a narrow workspace lifecycle API on an internal control
# network and intentionally contains no user/AI execution surface.
EXPOSE 8300
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
