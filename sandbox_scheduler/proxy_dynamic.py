"""动态容器请求代理"""
import logging
from datetime import datetime

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from .container_manager import DynamicContainerManager
from .config import SchedulerConfig


logger = logging.getLogger(__name__)


class DynamicRequestProxy:
    """动态请求代理
    
    根据 sandbox_id 动态路由到对应的容器，如果容器不存在则自动创建。
    """
    
    def __init__(self, manager: DynamicContainerManager, config: SchedulerConfig):
        self.manager = manager
        self.config = config
        self.client = httpx.AsyncClient(timeout=config.request_timeout)
    
    async def shutdown(self) -> None:
        """关闭代理"""
        await self.client.aclose()
    
    async def proxy_request(
        self,
        request: Request,
        sandbox_id: str,
    ) -> Response:
        """代理请求到容器
        
        Args:
            request: FastAPI 请求对象
            sandbox_id: Sandbox ID（必需）
            
        Returns:
            FastAPI Response 对象
        """
        # 获取或创建容器
        container = await self.manager.get_or_create_container(sandbox_id)
        
        if not container:
            return Response(
                content=f'{{"error": "Failed to create container for sandbox: {sandbox_id}"}}',
                status_code=503,
                media_type="application/json",
            )
        
        if not container.is_healthy:
            return Response(
                content=f'{{"error": "Container for sandbox {sandbox_id} is unhealthy"}}',
                status_code=503,
                media_type="application/json",
            )
        
        # 构建目标 URL
        target_url = f"{container.url}{request.url.path}"
        if request.url.query:
            target_url += f"?{request.url.query}"
        
        # 准备请求头
        headers = dict(request.headers)
        headers.pop("host", None)  # 移除原始 host
        
        # 添加调度信息到请求头
        headers["X-Scheduler-Container-Port"] = str(container.port)
        headers["X-Sandbox-ID"] = sandbox_id
        
        try:
            # 读取请求体
            body = await request.body()
            
            # 更新最后请求时间
            container.last_request_time = datetime.now()
            
            # 判断是否是流式请求
            is_streaming = (
                "/stream" in request.url.path or 
                request.headers.get("accept") == "text/event-stream"
            )

            logger.debug(f"请求路径: {request.url.path}, 是否流式: {is_streaming}")
            
            if is_streaming:
                return await self._proxy_streaming_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    body=body,
                    container_port=container.port,
                )
            else:
                return await self._proxy_regular_request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    body=body,
                    container_port=container.port,
                )
        
        except Exception as e:
            logger.error(f"代理请求失败: {e}", exc_info=True)
            container.total_requests += 1
            container.failed_requests += 1
            return Response(
                content=f'{{"error": "Request failed: {str(e)}"}}',
                status_code=500,
                media_type="application/json",
            )
    
    async def _proxy_regular_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        container_port: int,
    ) -> Response:
        """代理普通请求"""
        container = next(
            (c for c in self.manager.containers.values() if c.port == container_port),
            None
        )
        
        try:
            response = await self.client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
            
            if container:
                container.total_requests += 1
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
        
        except httpx.HTTPStatusError:
            if container:
                container.total_requests += 1
                container.failed_requests += 1
            raise
    
    async def _proxy_streaming_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        container_port: int,
    ) -> StreamingResponse:
        """代理流式请求"""
        container = next(
            (c for c in self.manager.containers.values() if c.port == container_port),
            None
        )
        
        async def stream_generator():
            try:
                async with self.client.stream(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                ) as response:
                    # 检查响应状态码
                    response_status = response.status_code
                    
                    # 如果响应不是成功状态，读取错误信息
                    if response_status >= 400:
                        error_content = await response.aread()
                        logger.error(f"流式请求失败，状态码: {response_status}, 错误: {error_content.decode()}")
                        if container:
                            container.total_requests += 1
                            container.failed_requests += 1
                        yield error_content
                        return
                    
                    # SSE 流应该按行迭代，不是按字节块
                    async for line in response.aiter_lines():
                        # 保持 SSE 格式，包括换行符
                        yield (line + "\n").encode()
                
                if container:
                    container.total_requests += 1
            
            except Exception as e:
                logger.error(f"流式请求失败: {e}", exc_info=True)
                if container:
                    container.total_requests += 1
                    container.failed_requests += 1
                # 发送错误事件，符合 SSE 格式
                error_msg = f'data: {{"type": "error", "error": "{str(e)}"}}\n\n'
                yield error_msg.encode()
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
        )
    
    def extract_sandbox_id(self, request: Request) -> str | None:
        """从请求中提取 sandbox_id
        
        优先级：
        1. 请求头中的 X-Sandbox-ID
        2. 查询参数中的 sandbox_id
        """
        # 从请求头提取
        sandbox_id = request.headers.get("X-Sandbox-ID")
        if sandbox_id:
            return sandbox_id
        
        # 从查询参数提取
        sandbox_id = request.query_params.get("sandbox_id")
        if sandbox_id:
            return sandbox_id
        
        return None

