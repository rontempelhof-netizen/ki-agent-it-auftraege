FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY config ./config

RUN useradd --create-home --uid 1000 agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app/data
USER agent

VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--help"]
