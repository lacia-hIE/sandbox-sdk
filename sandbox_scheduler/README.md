# Sandbox Scheduler (Dynamic)

一个**动态容器调度服务**，根据需求自动创建和管理 sandbox-container 实例，完全兼容现有的 Python SDK。

## 功能特性

- **动态容器管理**: 根据 `sandbox_id` 按需自动创建容器
- **容器隔离**: 每个 sandbox 拥有独立的容器实例和文件系统
- **自动清理**: 自动清理空闲容器，节省资源
- **健康检查**: 自动检测容器健康状态并进行故障转移
- **完全兼容**: 与现有 Python SDK 100% 兼容
- **无限扩展**: 理论上可以创建无限数量的容器（受限于配置）
- **持久化支持**: 基于 SQLite 的状态持久化，支持服务重启后自动恢复（新增）

## 架构

```
┌─────────────────────────────────┐
│ Python SDK (SandboxScheduler)   │
│  - get_sandbox("project-alpha")  │
│  - get_sandbox("project-beta")   │
│  - get_sandbox("project-gamma")  │
└────────────┬────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│  Scheduler Service (Port 8000)   │
│  动态容器管理器                     │
└────────┬─────────────────────────┘
         │
         │ 按需创建容器
         │
    ┌────┴──────────┬────────────┬────────────┐
    ▼               ▼            ▼            ▼
┌─────────┐   ┌─────────┐  ┌─────────┐  ┌─────────┐
│Container│   │Container│  │Container│  │Container│
│ alpha   │   │ beta    │  │ gamma   │  │ ...     │
│Port 3001│   │Port 3002│  │Port 3003│  │Port 300x│
└─────────┘   └─────────┘  └─────────┘  └─────────┘
     ↓              ↓           ↓             ↓
   Docker       Docker      Docker        Docker
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements-scheduler.txt
```

依赖包括：
- `fastapi` - Web 框架
- `uvicorn` - ASGI 服务器
- `httpx` - HTTP 客户端
- `pydantic` - 数据验证
- `docker` - Docker Python SDK

### 2. 准备容器镜像

确保本地镜像存在：

```bash
# 检查镜像
docker images | grep agent-loop-cf_sandbox

# 或者构建镜像
cd packages/sandbox-container
docker build -t agent-loop-cf_sandbox:latest -f ../sandbox/Dockerfile .
```

### 3. 启动调度服务

```bash
python -m sandbox_scheduler --port 8000
```

### 3. 使用 Python SDK

```python
from cf_sandbox import SandboxScheduler

# 创建调度器
scheduler = SandboxScheduler(base_url="http://localhost:8000")

# 获取 sandbox 实例（如果容器不存在会自动创建）
sandbox1 = scheduler.get_sandbox("project-alpha")
sandbox2 = scheduler.get_sandbox("project-beta")

# 每个 sandbox 有独立的容器和文件系统
session1 = await sandbox1.create_session(id="session-1")
session2 = await sandbox2.create_session(id="session-2")

# 执行操作
result1 = await session1.execute_command("echo 'Hello from Alpha'")
result2 = await session2.execute_command("echo 'Hello from Beta'")
```

## 配置选项

### 基本配置
- `--port`: 调度服务监听端口（默认: 8000）
- `--container-image`: Docker 容器镜像名称（默认: agent-loop-cf_sandbox:latest）
- `--container-base-port`: 容器起始端口（默认: 10000）
- `--max-containers`: 最大容器数量限制（默认: 100）
- `--container-idle-timeout`: 容器空闲超时秒数（默认: 3600）
- `--health-check-interval`: 健康检查间隔秒数（默认: 30）
- `--no-auto-cleanup`: 禁用自动清理空闲容器
- `--no-health-check`: 禁用健康检查

### 持久化配置（新增）
- `--no-persistence`: 禁用持久化（默认启用）
- `--database-path`: 数据库文件路径（默认: scheduler.db）
- `--no-auto-restore`: 禁用启动时自动恢复容器状态（默认启用）

## 工作原理

### 容器生命周期

1. **创建**: 当调用 `scheduler.get_sandbox(sandbox_id)` 时，如果容器不存在，自动创建
2. **运行**: 容器处理该 sandbox 的所有请求
3. **空闲**: 超过 `container_idle_timeout` 没有请求
4. **清理**: 自动停止并删除空闲容器（如果启用自动清理）

### 路由机制

- 所有请求通过 `X-Sandbox-ID` 请求头标识目标 sandbox
- 调度器根据 `sandbox_id` 路由到对应的容器
- 如果容器不存在，自动创建新容器
- 如果容器不健康，自动重启

### API 端点

#### 调度器管理

- `GET /`: 服务信息
- `GET /health`: 健康检查
- `GET /stats`: 统计信息
- `GET /containers`: 列出所有容器
- `GET /containers/{sandbox_id}`: 获取容器信息
- `POST /containers/{sandbox_id}/stop`: 停止容器

#### 容器 API 代理

- `ALL /api/*`: 代理所有请求到对应容器（需要 `X-Sandbox-ID` 头）

## 高级用法

### 获取调度器统计信息

```python
stats = await scheduler.get_scheduler_stats()
print(f"总容器数: {stats['total_containers']}")
print(f"健康容器数: {stats['healthy_containers']}")
```

### 手动停止容器

```python
await scheduler.manager.stop_container("project-alpha")
```

### 列出所有活跃的 sandbox

```python
sandbox_ids = scheduler.list_sandboxes()
print(f"活跃 Sandbox: {sandbox_ids}")
```

## 故障处理

- **容器创建失败**: 自动重试，返回错误信息
- **容器不健康**: 自动重启容器
- **端口冲突**: 自动分配新端口
- **资源不足**: 返回 503 错误，等待资源释放
