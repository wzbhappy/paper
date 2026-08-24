# 生产后端镜像：不带 --reload，构建后直接运行。
# 开发用 backend/Dockerfile（带 --reload + 源码挂载），见 docker-compose.yml。

FROM python:3.11-slim

WORKDIR /code

COPY backend/pyproject.toml ./
COPY backend/app ./app

RUN pip install --no-cache-dir -e .

ENV STORAGE_DIR=/data/papers \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data/papers

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
