#!/bin/bash
# Sandbox Scheduler (Dynamic) 启动脚本
# 容器会根据需求动态创建

set -e

# 默认配置
HOST="${SCHEDULER_HOST:-0.0.0.0}"
PORT="${SCHEDULER_PORT:-8000}"
CONTAINER_IMAGE="${CONTAINER_IMAGE:-agent-loop-cf_sandbox:latest}"
CONTAINER_BASE_PORT="${CONTAINER_BASE_PORT:-3001}"
CONTAINER_HOST="${CONTAINER_HOST:-localhost}"
MAX_CONTAINERS="${MAX_CONTAINERS:-100}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "========================================"
echo "启动 Sandbox Scheduler (Dynamic)"
echo "========================================"
echo "服务地址: $HOST:$PORT"
echo "容器镜像: $CONTAINER_IMAGE"
echo "容器起始端口: $CONTAINER_BASE_PORT"
echo "容器主机: $CONTAINER_HOST"
echo "最大容器数: $MAX_CONTAINERS"
echo "日志级别: $LOG_LEVEL"
echo "========================================"
echo ""

# 启动调度器
python -m sandbox_scheduler \
    --host "$HOST" \
    --port "$PORT" \
    --container-image "$CONTAINER_IMAGE" \
    --container-base-port "$CONTAINER_BASE_PORT" \
    --container-host "$CONTAINER_HOST" \
    --max-containers "$MAX_CONTAINERS" \
    --log-level "$LOG_LEVEL" \
    "$@"

