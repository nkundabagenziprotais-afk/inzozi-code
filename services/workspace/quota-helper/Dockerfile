FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends xfsprogs quota util-linux coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /helper
COPY app ./app

ENTRYPOINT ["python", "-m", "app.main"]
