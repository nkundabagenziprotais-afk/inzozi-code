FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /proxy

COPY app ./app

RUN useradd --create-home --uid 10004 proxy

USER 10004:10004

CMD ["python", "-m", "app.proxy"]
