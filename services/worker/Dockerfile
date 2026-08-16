FROM node:22.22.1-bookworm-slim AS hyperframes
ARG HYPERFRAMES_VERSION=0.7.109
ARG CHROME_VERSION=151.0.7922.71
ARG CHROME_SHA256=7dd9d23b46fa7a9bfa26f1af96f413e0514c32698f6a43a57e1ade48d88a6578
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*
RUN npm install --global "hyperframes@${HYPERFRAMES_VERSION}"
RUN curl -fsSL -o /tmp/chrome.zip "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip" && echo "${CHROME_SHA256}  /tmp/chrome.zip" | sha256sum --check --strict && mkdir -p /opt/chrome && unzip -q /tmp/chrome.zip -d /opt/chrome && rm /tmp/chrome.zip

FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/app/.venv/bin:/usr/local/bin:$PATH
ENV CONTAINER=true
ENV NPM_CONFIG_OFFLINE=true
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PRODUCER_HEADLESS_SHELL_PATH=/opt/chrome/chrome-headless-shell-linux64/chrome-headless-shell
ENV CINEMATIC_MUSIC_PATH=/app/assets/music/cinematic.mp3
ENV TMPDIR=/app/tmp
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ffmpeg libasound2 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libgtk-3-0 libnss3 libpangocairo-1.0-0 libxcomposite1 libxdamage1 libxrandr2 libxshmfence1 fonts-dejavu-core fonts-freefont-ttf fonts-liberation fonts-noto-cjk fonts-noto-color-emoji fonts-noto-core fonts-noto-extra fonts-noto-ui-core fontconfig && rm -rf /var/lib/apt/lists/* && fc-cache -f
COPY --from=uv /uv /uvx /bin/
COPY --from=hyperframes /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=hyperframes /usr/local/bin/node /usr/local/bin/node
COPY --from=hyperframes /usr/local/bin/npm /usr/local/bin/npm
COPY --from=hyperframes /usr/local/bin/npx /usr/local/bin/npx
COPY --from=hyperframes /usr/local/bin/hyperframes /usr/local/bin/hyperframes
COPY --from=hyperframes /opt/chrome /opt/chrome
WORKDIR /app
COPY shared/pyproject.toml /app/shared/pyproject.toml
COPY shared/src /app/shared/src
COPY services/worker/pyproject.toml /app/services/worker/pyproject.toml
COPY services/worker/src /app/services/worker/src
COPY services/worker/assets/music/cinematic.mp3 /app/assets/music/cinematic.mp3
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python /app/shared /app/services/worker
RUN npx hyperframes --version && test -x "$PRODUCER_HEADLESS_SHELL_PATH" && test -s /app/assets/music/cinematic.mp3
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --home-dir /app --no-create-home app && mkdir -p /app/tmp && chown -R app:app /app /opt/chrome
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "campaign_worker.main"]
