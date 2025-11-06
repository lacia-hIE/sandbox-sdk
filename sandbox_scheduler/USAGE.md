# Sandbox Scheduler 使用指南

本文档详细说明如何使用动态容器调度服务。

## 核心概念

### Sandbox ID
每个 `sandbox_id` 对应一个独立的容器实例。同一个 `sandbox_id` 的所有请求都会被路由到同一个容器。

### 容器生命周期
- 当首次访问某个 `sandbox_id` 时，调度器会自动创建对应的 Docker 容器
- 容器会持续运行，处理该 sandbox 的所有请求
- 如果容器空闲超过配置的超时时间，会被自动清理（如果启用）

## 完整示例

### 1. 基本使用

```python
import asyncio
from cf_sandbox import SandboxScheduler

async def main():
    # 创建调度器
    scheduler = SandboxScheduler(base_url="http://localhost:8000")
    
    try:
        # 获取 sandbox 实例（容器会自动创建）
        sandbox = scheduler.get_sandbox("my-project")
        
        # 创建会话
        session = await sandbox.create_session(id="session-1")
        
        # 执行命令
        result = await session.execute_command("echo 'Hello World'")
        print(result.stdout)
        
    finally:
        await scheduler.close()

asyncio.run(main())
```

### 2. 多项目隔离

```python
# 不同项目使用不同的 sandbox_id
# 每个项目有独立的容器和文件系统

sandbox_project_a = scheduler.get_sandbox("project-a")
sandbox_project_b = scheduler.get_sandbox("project-b")

# 在 project-a 中创建文件
await sandbox_project_a.write_file("/workspace/data.txt", "Data A")

# 在 project-b 中创建文件
await sandbox_project_b.write_file("/workspace/data.txt", "Data B")

# 两个文件是隔离的，互不影响
content_a = await sandbox_project_a.read_file("/workspace/data.txt")
content_b = await sandbox_project_b.read_file("/workspace/data.txt")

print(content_a.content)  # "Data A"
print(content_b.content)  # "Data B"
```

### 3. 多用户场景

```python
# 为每个用户创建独立的 sandbox
def get_user_sandbox(scheduler, user_id: str):
    sandbox_id = f"user-{user_id}"
    return scheduler.get_sandbox(sandbox_id)

# 用户 1
user1_sandbox = get_user_sandbox(scheduler, "alice")
session1 = await user1_sandbox.create_session(id="session-alice")

# 用户 2  
user2_sandbox = get_user_sandbox(scheduler, "bob")
session2 = await user2_sandbox.create_session(id="session-bob")

# 每个用户有独立的运行环境
```

### 4. 监控和管理

```python
# 获取调度器统计信息
stats = await scheduler.get_scheduler_stats()
print(f"当前容器数: {stats['total_containers']}")
print(f"健康容器数: {stats['healthy_containers']}")

# 列出所有容器
for container in stats['containers']:
    print(f"Sandbox: {container['sandbox_id']}")
    print(f"  端口: {container['port']}")
    print(f"  状态: {container['status']}")
    print(f"  请求数: {container['total_requests']}")
    print(f"  成功率: {container['success_rate']:.2%}")

# 手动停止特定容器
import httpx
async with httpx.AsyncClient() as client:
    await client.post("http://localhost:8000/containers/project-a/stop")
```

### 5. 流式操作

```python
# 流式执行命令
sandbox = scheduler.get_sandbox("streaming-project")
session = await sandbox.create_session(id="stream-session")

async for event in session.execute_command_stream("npm install"):
    if event.type == "stdout":
        print(event.data, end="")
    elif event.type == "stderr":
        print(f"错误: {event.data}", file=sys.stderr)
    elif event.type == "complete":
        print(f"\n命令完成，退出码: {event.exit_code}")
```

### 6. 代码执行

```python
# 使用代码解释器
sandbox = scheduler.get_sandbox("code-project")

# 创建 Python 代码上下文
context = await sandbox.create_code_context(
    language="python",
    cwd="/workspace"
)

# 执行代码
code = """
import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 10, 100)
y = np.sin(x)

plt.plot(x, y)
plt.title("Sine Wave")
plt.savefig("sine.png")
print("图表已保存")
"""

async for event in sandbox.execute_code(context.id, code):
    if event.type == "stdout":
        print(event.stdout)
    elif event.type == "result":
        # 处理执行结果
        if event.png_base64:
            # 保存图片
            import base64
            with open("output.png", "wb") as f:
                f.write(base64.b64decode(event.png_base64))
```

## 最佳实践

### 1. Sandbox ID 命名规范

```python
# 建议使用清晰的命名规范
sandbox_id = f"{organization}-{project}-{environment}"
# 例如: "acme-webapp-dev", "acme-api-prod"
```

### 2. 资源管理

```python
# 使用上下文管理器确保资源释放
class SandboxContext:
    def __init__(self, scheduler, sandbox_id):
        self.scheduler = scheduler
        self.sandbox_id = sandbox_id
        self.sandbox = None
    
    async def __aenter__(self):
        self.sandbox = self.scheduler.get_sandbox(self.sandbox_id)
        return self.sandbox
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 可选：停止容器释放资源
        pass

# 使用
async with SandboxContext(scheduler, "temp-project") as sandbox:
    session = await sandbox.create_session(id="temp")
    result = await session.execute_command("echo 'test'")
# 退出时自动清理
```

### 3. 错误处理

```python
from cf_sandbox.exceptions import SandboxAPIError

try:
    sandbox = scheduler.get_sandbox("my-project")
    session = await sandbox.create_session(id="session-1")
    result = await session.execute_command("invalid-command")
except SandboxAPIError as e:
    print(f"API 错误: {e.message} (状态码: {e.status_code})")
except Exception as e:
    print(f"未知错误: {e}")
```

### 4. 并发请求

```python
# 并发处理多个 sandbox
import asyncio

async def process_sandbox(sandbox_id: str):
    sandbox = scheduler.get_sandbox(sandbox_id)
    session = await sandbox.create_session(id=f"session-{sandbox_id}")
    result = await session.execute_command("echo 'Processing'")
    return result

# 并发执行
sandbox_ids = ["project-1", "project-2", "project-3"]
results = await asyncio.gather(*[
    process_sandbox(sid) for sid in sandbox_ids
])
```

## 故障排查

### 容器创建失败

```bash
# 检查 Docker 是否运行
docker ps

# 检查镜像是否存在
docker images | grep sandbox-container

# 查看调度器日志
python -m sandbox_scheduler --log-level DEBUG
```

### 端口冲突

```bash
# 检查端口占用
lsof -i :3001-3100

# 修改起始端口
python -m sandbox_scheduler --container-base-port 4001
```

### 容器无响应

```python
# 检查容器健康状态
health = await scheduler.get_scheduler_health()
for container in health['stats']['containers']:
    if container['status'] != 'healthy':
        print(f"不健康的容器: {container['sandbox_id']}")
```

## 性能优化

### 1. 预热容器

```python
# 提前创建常用的容器
common_sandboxes = ["main", "staging", "testing"]
for sandbox_id in common_sandboxes:
    sandbox = scheduler.get_sandbox(sandbox_id)
    await sandbox.ping()  # 触发容器创建
```

### 2. 调整超时设置

```bash
# 增加空闲超时，减少容器频繁创建/销毁
python -m sandbox_scheduler --container-idle-timeout 7200  # 2小时
```

### 3. 限制容器数量

```bash
# 防止资源耗尽
python -m sandbox_scheduler --max-containers 50
```

## 安全建议

1. **网络隔离**: 在生产环境中，调度器应在内网运行
2. **认证授权**: 在调度器前添加认证层（如 API Gateway）
3. **资源限制**: 为 Docker 容器设置 CPU 和内存限制
4. **定期清理**: 启用自动清理，防止容器堆积

```bash
# 启用自动清理
python -m sandbox_scheduler --container-idle-timeout 1800  # 30分钟空闲后清理
```

