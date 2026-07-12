FROM node:22-alpine AS web
WORKDIR /src/web_ui
COPY web_ui/package.json web_ui/package-lock.json web_ui/tsconfig.json web_ui/index.html web_ui/build.mjs ./
COPY web_ui/src ./src
RUN npm ci && npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY wechat_archive ./wechat_archive
COPY config.yaml run.py ./
COPY --from=web /src/web_ui/dist ./web_ui/dist
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["wechat-archive", "serve", "--host", "0.0.0.0", "--port", "8000"]
