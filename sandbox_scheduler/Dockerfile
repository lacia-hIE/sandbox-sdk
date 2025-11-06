FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements-scheduler.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements-scheduler.txt

# 复制调度器代码
COPY sandbox_scheduler/ ./sandbox_scheduler/
COPY cf_sandbox/ ./cf_sandbox/

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动调度器
CMD ["python", "-m", "sandbox_scheduler", "--host", "0.0.0.0", "--port", "8000"]

