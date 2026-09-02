FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && \
    addgroup --system --gid 10001 modelfleet && \
    adduser --system --uid 10001 --ingroup modelfleet modelfleet

USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "modelfleet.operator"]
