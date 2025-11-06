"""动态容器调度服务 HTTP 服务器"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse

from .config import SchedulerConfig
from .container_manager import DynamicContainerManager
from .proxy_dynamic import DynamicRequestProxy


logger = logging.getLogger(__name__)


def create_app(config: SchedulerConfig) -> FastAPI:
    """创建 FastAPI 应用"""

    # 初始化组件
    manager: DynamicContainerManager | None = None
    proxy: DynamicRequestProxy | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期"""
        nonlocal manager, proxy
        logger.info("启动动态调度服务...")

        manager = DynamicContainerManager(config)
        await manager.initialize()

        proxy = DynamicRequestProxy(manager, config)

        app.state.manager = manager
        app.state.proxy = proxy

        logger.info(f"动态调度服务已启动在 {config.host}:{config.port}")
        yield
        logger.info("关闭动态调度服务...")

        if proxy:
            await proxy.shutdown()
        if manager:
            await manager.shutdown()

        logger.info("动态调度服务已关闭")

    app = FastAPI(
        lifespan=lifespan,
    )

    # @app.on_event("startup")
    # async def startup():
    #     nonlocal manager, proxy
    #     logger.info("启动动态调度服务...")

    #     manager = DynamicContainerManager(config)
    #     await manager.initialize()

    #     proxy = DynamicRequestProxy(manager, config)

    #     app.state.manager = manager
    #     app.state.proxy = proxy

    #     logger.info(f"动态调度服务已启动在 {config.host}:{config.port}")

    # @app.on_event("shutdown")
    # async def shutdown():
    #     logger.info("关闭动态调度服务...")

    #     if proxy:
    #         await proxy.shutdown()
    #     if manager:
    #         await manager.shutdown()

    #     logger.info("动态调度服务已关闭")

    @app.get("/")
    async def root():
        """根路径"""
        return {
            "service": "Sandbox Scheduler (Dynamic)",
            "version": "0.2.0",
            "status": "running",
            "mode": "dynamic",
        }

    @app.get("/health")
    async def health():
        """健康检查"""
        manager: DynamicContainerManager = app.state.manager
        stats = manager.get_stats()

        is_healthy = True  # 调度器本身总是健康的

        return JSONResponse(
            content={
                "status": "healthy" if is_healthy else "unhealthy",
                "stats": stats,
            },
            status_code=200,
        )

    @app.get("/stats")
    async def stats():
        """获取统计信息"""
        manager: DynamicContainerManager = app.state.manager
        return manager.get_stats()

    @app.get("/containers")
    async def list_containers():
        """列出所有容器"""
        manager: DynamicContainerManager = app.state.manager
        containers = await manager.list_containers()

        return {
            "containers": [
                {
                    "sandbox_id": sandbox_id,
                    "port": container.port,
                    "url": container.url,
                    "status": container.status.value,
                    "total_requests": container.total_requests,
                    "failed_requests": container.failed_requests,
                    "success_rate": container.success_rate,
                }
                for sandbox_id, container in containers.items()
            ],
            "total": len(containers),
        }

    @app.post("/containers/{sandbox_id}/stop")
    async def stop_container(sandbox_id: str):
        """停止容器"""
        manager: DynamicContainerManager = app.state.manager
        success = await manager.stop_container(sandbox_id)

        if success:
            return {"message": f"Container {sandbox_id} stopped successfully"}
        else:
            raise HTTPException(
                status_code=404, detail=f"Container {sandbox_id} not found"
            )

    @app.get("/containers/{sandbox_id}")
    async def get_container(sandbox_id: str):
        """获取容器信息"""
        manager: DynamicContainerManager = app.state.manager
        container = manager.get_container(sandbox_id)

        if not container:
            raise HTTPException(
                status_code=404, detail=f"Container {sandbox_id} not found"
            )

        # 获取 Docker 容器信息
        docker_container = manager.get_docker_container(sandbox_id)
        docker_info = None
        if docker_container:
            docker_info = {
                "id": docker_container.id[:12],
                "status": docker_container.status,
                "created": docker_container.attrs.get("Created"),
            }

        return {
            "sandbox_id": sandbox_id,
            "port": container.port,
            "url": container.url,
            "status": container.status.value,
            "total_requests": container.total_requests,
            "failed_requests": container.failed_requests,
            "success_rate": container.success_rate,
            "last_health_check": (
                container.last_health_check.isoformat()
                if container.last_health_check
                else None
            ),
            "last_request_time": container.last_request_time.isoformat(),
            "docker": docker_info,
        }

    @app.get("/docker/containers")
    async def list_docker_containers():
        """列出所有 Docker 容器（包括调度器管理的）"""
        manager: DynamicContainerManager = app.state.manager
        containers = manager.list_docker_containers()

        return {
            "containers": [
                {
                    "id": c.id[:12],
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image.tags else c.image.id[:12],
                    "labels": c.labels,
                    "ports": c.ports,
                }
                for c in containers
            ],
            "total": len(containers),
        }

    # 所有 API 请求都通过代理转发
    # sandbox_id 通过请求头 X-Sandbox-ID 传递
    @app.api_route(
        "/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
    )
    async def proxy_api(request: Request, path: str):
        """代理所有 API 请求到对应的容器"""
        proxy: DynamicRequestProxy = app.state.proxy

        # 提取 sandbox_id
        sandbox_id = proxy.extract_sandbox_id(request)

        if not sandbox_id:
            return JSONResponse(
                content={
                    "error": "Missing sandbox_id",
                    "message": "Please provide sandbox_id via X-Sandbox-ID header or sandbox_id query parameter",
                },
                status_code=400,
            )

        return await proxy.proxy_request(request, sandbox_id=sandbox_id)

    return app
