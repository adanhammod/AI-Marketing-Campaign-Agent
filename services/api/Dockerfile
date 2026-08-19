FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY shared/pyproject.toml /app/shared/pyproject.toml
COPY shared/src /app/shared/src
COPY services/api/pyproject.toml /app/services/api/pyproject.toml
COPY services/api/src /app/services/api/src
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python /app/shared /app/services/api
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --home-dir /app --no-create-home app && chown -R app:app /app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "campaign_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
