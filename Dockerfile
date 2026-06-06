FROM python:3.11-slim

WORKDIR /app

# ── 依赖层（先复制 requirements 利用构建缓存）─────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 创建非 root 用户 ────────────────────────────────────────────────────
RUN useradd -r -u 1001 -g root -s /bin/false appuser

# ── 复制应用代码 ────────────────────────────────────────────────────────
# config/ 在生产环境由 docker-compose volume 覆盖，这里作为模板备用
COPY --chown=appuser:appuser bot/     ./bot/
COPY --chown=appuser:appuser config/  ./config/

USER appuser

# -u：强制 stdout/stderr 无缓冲，确保日志实时输出到 docker logs
CMD ["python3", "-u", "bot/main.py"]
