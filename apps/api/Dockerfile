FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# --no-proxy-headers keeps request.client.host as the immediate TCP peer (nginx).
# Application-level AUTH_TRUSTED_PROXIES then decides whether X-Real-IP may be used.
# Uvicorn must not rewrite the peer from X-Forwarded-For.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
