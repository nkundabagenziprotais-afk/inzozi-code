FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

# Git checkouts created under a restrictive host umask can legitimately
# present tracked source files as 0600/directories as 0700 in the Docker build
# context. Normalize only the copied application source inside the image so the
# non-root runtime user can always traverse and read it. The repository checkout
# itself remains untouched.
RUN find /app/app -type d -exec chmod 0755 {} + \
    && find /app/app -type f -exec chmod 0644 {} +

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# --no-proxy-headers keeps request.client.host as the immediate TCP peer (nginx).
# Application-level AUTH_TRUSTED_PROXIES then decides whether X-Real-IP may be used.
# Uvicorn must not rewrite the peer from X-Forwarded-For.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
