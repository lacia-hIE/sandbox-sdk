"""动态容器管理器"""
import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
import docker
from docker.errors import DockerException, NotFound, APIError

from .config import SchedulerConfig
from .database import Database
from .types import ContainerInfo, ContainerStatus


logger = logging.getLogger(__name__)


class DynamicContainerManager:
    """动态容器管理器
    
    根据需求动态创建和销毁容器，每个 sandbox_id 对应一个独立的容器实例。
    """
    
    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.containers: dict[str, ContainerInfo] = {}  # sandbox_id -> ContainerInfo
        self.sandbox_to_port: dict[str, int] = {}  # sandbox_id -> port
        self.used_ports: set[int] = set()
        self._port_counter = config.container_base_port
        self._lock = asyncio.Lock()
        self._health_check_task: asyncio.Task[None] | None = None
        self._http_client = httpx.AsyncClient(timeout=config.health_check_timeout)
        
        # 初始化持久化层
        self._db: Database | None = None
        if config.persistence_enabled:
            try:
                self._db = Database(config.database_path)
                logger.info(f"持久化已启用，数据库路径: {config.database_path}")
            except Exception as e:
                logger.error(f"初始化数据库失败: {e}", exc_info=True)
                # 持久化失败不影响服务启动，只是不会持久化
                self._db = None
        
        # 初始化 Docker 客户端
        try:
            self._docker_client = docker.from_env()
            logger.info("Docker 客户端已初始化")
        except DockerException as e:
            logger.error(f"无法连接到 Docker: {e}")
            raise
        
    async def initialize(self) -> None:
        """初始化容器管理器"""
        logger.info("初始化动态容器管理器")
        
        # 从数据库恢复容器状态
        if self._db and self.config.auto_restore:
            await self._restore_containers_from_db()
        
        # 启动健康检查
        if self.config.health_check_enabled:
            self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("动态容器管理器已就绪")
    
    async def shutdown(self) -> None:
        """关闭容器管理器
        
        根据配置决定是否停止容器：
        - 如果 stop_containers_on_shutdown=True，停止所有容器
        - 如果 stop_containers_on_shutdown=False（默认），保留容器继续运行
        """
        logger.info("关闭动态容器管理器")
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # 根据配置决定是否停止容器
        if self.config.stop_containers_on_shutdown:
            logger.warning("配置为关闭时停止所有容器")
            await self.stop_all_containers()
        else:
            # 保存所有容器状态到数据库
            if self._db:
                logger.info(f"保存 {len(self.containers)} 个容器的状态到数据库")
                for sandbox_id, container in self.containers.items():
                    try:
                        self._db.save_container(sandbox_id, container)
                    except Exception as e:
                        logger.error(f"保存容器 {sandbox_id} 状态失败: {e}")
            
            # 不停止容器，让它们继续运行
            logger.info(f"保留 {len(self.containers)} 个运行中的容器")
        
        await self._http_client.aclose()
        
        # 关闭 Docker 客户端
        try:
            self._docker_client.close()
        except Exception as e:
            logger.error(f"关闭 Docker 客户端失败: {e}")
        
        status_msg = "已停止容器" if self.config.stop_containers_on_shutdown else "容器继续运行"
        logger.info(f"动态容器管理器已关闭（{status_msg}）")
    
    async def get_or_create_container(self, sandbox_id: str) -> ContainerInfo | None:
        """获取或创建容器
        
        Args:
            sandbox_id: Sandbox 唯一标识符
            
        Returns:
            容器信息，如果创建失败则返回 None
        """
        async with self._lock:
            # 如果容器已存在且健康，直接返回
            if sandbox_id in self.containers:
                container = self.containers[sandbox_id]
                if container.is_healthy:
                    logger.debug(f"使用已存在的容器: {sandbox_id}")
                    return container
                else:
                    logger.warning(f"容器 {sandbox_id} 不健康，尝试重启")
                    await self._stop_container_internal(sandbox_id)
            
            # 创建新容器
            logger.info(f"为 {sandbox_id} 创建新容器")
            return await self._create_container_internal(sandbox_id)
    
    async def stop_container(self, sandbox_id: str) -> bool:
        """停止容器
        
        Args:
            sandbox_id: Sandbox 标识符
            
        Returns:
            是否成功停止
        """
        async with self._lock:
            return await self._stop_container_internal(sandbox_id)
    
    async def list_containers(self) -> dict[str, ContainerInfo]:
        """列出所有容器"""
        return dict(self.containers)
    
    def get_container(self, sandbox_id: str) -> ContainerInfo | None:
        """获取容器信息"""
        return self.containers.get(sandbox_id)
    
    async def _create_container_internal(self, sandbox_id: str) -> ContainerInfo | None:
        """内部方法：创建容器（需要在锁内调用）"""
        # 分配端口
        port = self._allocate_port()
        if not port:
            logger.error("无法分配端口")
            return None
        
        try:
            # 创建容器信息
            url = self.config.get_container_url(port)
            container = ContainerInfo(
                port=port,
                url=url,
                status=ContainerStatus.INITIALIZING,
            )
            
            # 启动容器
            success = await self._start_docker_container(sandbox_id, port)
            if not success:
                self._release_port(port)
                return None
            
            # 等待容器就绪
            if not await self._wait_for_container_ready(container):
                await self._stop_docker_container(sandbox_id)
                self._release_port(port)
                return None
            
            # 记录容器
            container.status = ContainerStatus.HEALTHY
            self.containers[sandbox_id] = container
            self.sandbox_to_port[sandbox_id] = port
            
            # 持久化到数据库
            if self._db:
                self._db.save_container(sandbox_id, container)
            
            logger.info(f"容器 {sandbox_id} 已创建并就绪，端口: {port}")
            return container
        
        except Exception as e:
            logger.error(f"创建容器 {sandbox_id} 失败: {e}", exc_info=True)
            self._release_port(port)
            return None
    
    async def _stop_container_internal(self, sandbox_id: str) -> bool:
        """内部方法：停止容器（需要在锁内调用）"""
        if sandbox_id not in self.containers:
            return False
        
        container = self.containers[sandbox_id]
        port = container.port
        
        try:
            # 停止 Docker 容器
            await self._stop_docker_container(sandbox_id)
            
            # 释放资源
            del self.containers[sandbox_id]
            if sandbox_id in self.sandbox_to_port:
                del self.sandbox_to_port[sandbox_id]
            self._release_port(port)
            
            # 从数据库删除
            if self._db:
                self._db.delete_container(sandbox_id)
            
            logger.info(f"容器 {sandbox_id} 已停止")
            return True
        
        except Exception as e:
            logger.error(f"停止容器 {sandbox_id} 失败: {e}", exc_info=True)
            return False
    
    async def _start_docker_container(self, sandbox_id: str, port: int) -> bool:
        """启动 Docker 容器"""
        container_name = f"sandbox-{sandbox_id}"
        
        try:
            # # 检查容器是否已存在
            # try:
            #     existing = self._docker_client.containers.get(container_name)
            #     logger.warning(f"容器 {container_name} 已存在，先删除")
            #     existing.remove(force=True)
            # except NotFound:
            #     pass  # 容器不存在，正常
            
            # 创建并启动容器
            # 在后台线程中执行（Docker SDK 是同步的）
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None,
                lambda: self._docker_client.containers.run(
                    image=self.config.container_image,
                    name=container_name,
                    detach=True,
                    ports={'3000/tcp': port},
                    environment={
                        'NODE_ENV': 'production',
                    },
                    # volumes={
                    #     f'sandbox-{sandbox_id}': {
                    #         'bind': '/workspace',
                    #         'mode': 'rw'
                    #     }
                    # },
                    labels={
                        'sandbox.id': sandbox_id,
                        'sandbox.managed': 'true',
                    },
                    # 资源限制（可选）
                    # mem_limit=self.config.container_memory_limit if hasattr(self.config, 'container_memory_limit') else None,
                    # cpu_period=100000,
                    # cpu_quota=self.config.container_cpu_quota if hasattr(self.config, 'container_cpu_quota') else None,
                )
            )
            
            logger.info(f"Docker 容器已启动: {container_name} (ID: {container.id[:12]}, Port: {port})")
            return True
        
        except DockerException as e:
            logger.error(f"启动 Docker 容器失败: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"启动 Docker 容器异常: {e}", exc_info=True)
            return False
    
    async def _stop_docker_container(self, sandbox_id: str) -> bool:
        """停止并删除 Docker 容器"""
        container_name = f"sandbox-{sandbox_id}"
        
        try:
            # 在后台线程中执行
            loop = asyncio.get_event_loop()
            
            def stop_and_remove():
                try:
                    container = self._docker_client.containers.get(container_name)
                    # 停止容器（超时 10 秒）
                    container.stop(timeout=10)
                    # 删除容器
                    container.remove()
                    return True
                except NotFound:
                    logger.warning(f"容器 {container_name} 不存在")
                    return False
                except APIError as e:
                    logger.error(f"停止容器 {container_name} 失败: {e}")
                    # 强制删除
                    try:
                        container = self._docker_client.containers.get(container_name)
                        container.remove(force=True)
                        return True
                    except Exception:
                        return False
            
            success = await loop.run_in_executor(None, stop_and_remove)
            
            if success:
                logger.info(f"Docker 容器已删除: {container_name}")
            
            return success
        
        except Exception as e:
            logger.error(f"停止 Docker 容器异常: {e}", exc_info=True)
            return False
    
    async def _wait_for_container_ready(
        self, 
        container: ContainerInfo,
        timeout: int = 60,
    ) -> bool:
        """等待容器就绪"""
        logger.info(f"等待容器 {container.port} 就绪...")
        start_time = datetime.now()
        
        while True:
            # 检查容器健康状态
            try:
                url = f"{container.url}/api/ping"
                response = await self._http_client.get(url)
                
                if response.status_code == 200:
                    return True
            except Exception:
                pass  # 忽略错误，继续等待
            
            # 检查超时
            elapsed = (datetime.now() - start_time).total_seconds()
            if elapsed > timeout:
                logger.error(f"等待容器 {container.port} 就绪超时")
                return False
            
            await asyncio.sleep(2)
    
    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        logger.info("启动容器健康检查循环")
        
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._perform_health_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查循环出错: {e}", exc_info=True)
    
    async def _perform_health_checks(self) -> None:
        """执行健康检查"""
        sandbox_ids = list(self.containers.keys())
        
        for sandbox_id in sandbox_ids:
            container = self.containers.get(sandbox_id)
            if not container:
                continue
            
            try:
                url = f"{container.url}/api/ping"
                response = await self._http_client.get(url)
                
                if response.status_code == 200:
                    if container.status != ContainerStatus.HEALTHY:
                        logger.info(f"容器 {sandbox_id} 恢复健康")
                    container.status = ContainerStatus.HEALTHY
                    container.consecutive_failures = 0
                else:
                    container.consecutive_failures += 1
            
            except Exception as e:
                container.consecutive_failures += 1
                logger.debug(f"容器 {sandbox_id} 健康检查失败: {e}")
            
            # 如果连续失败太多次，标记为不健康
            if container.consecutive_failures >= self.config.health_check_retries:
                if container.status == ContainerStatus.HEALTHY:
                    logger.error(f"容器 {sandbox_id} 标记为不健康")
                container.status = ContainerStatus.UNHEALTHY
            
            container.last_health_check = datetime.now()
            
            # 更新到数据库
            if self._db:
                self._db.save_container(sandbox_id, container)
    
    def _allocate_port(self) -> int | None:
        """分配端口"""
        # 查找可用端口
        for _ in range(1000):  # 最多尝试 1000 次
            port = self._port_counter
            self._port_counter += 1
            
            # 端口范围检查
            if port > 65535:
                self._port_counter = self.config.container_base_port
                port = self._port_counter
                self._port_counter += 1
            
            if port not in self.used_ports:
                self.used_ports.add(port)
                return port
        
        logger.error("无法分配端口：所有端口都已使用")
        return None
    
    def _release_port(self, port: int) -> None:
        """释放端口"""
        self.used_ports.discard(port)
    
    async def _restore_containers_from_db(self) -> None:
        """从数据库恢复容器状态
        
        在启动时，从数据库加载之前保存的容器信息，并验证容器是否仍然存在。
        如果容器已经不存在，则从数据库中删除。
        """
        if not self._db:
            return
        
        logger.info("开始从数据库恢复容器状态...")
        
        try:
            # 加载所有容器信息
            saved_containers = self._db.load_all_containers()
            
            if not saved_containers:
                logger.info("数据库中没有保存的容器")
                return
            
            restored_count = 0
            removed_count = 0
            
            for sandbox_id, container in saved_containers.items():
                # 检查 Docker 容器是否仍然存在
                docker_container = self.get_docker_container(sandbox_id)
                
                if docker_container:
                    # 容器存在，检查其状态
                    try:
                        docker_container.reload()
                        if docker_container.status == 'running':
                            # 恢复容器信息到内存
                            self.containers[sandbox_id] = container
                            self.sandbox_to_port[sandbox_id] = container.port
                            self.used_ports.add(container.port)
                            
                            # 更新端口计数器
                            if container.port >= self._port_counter:
                                self._port_counter = container.port + 1
                            
                            logger.info(f"恢复容器 {sandbox_id} (端口: {container.port})")
                            restored_count += 1
                        else:
                            # 容器已停止，清理
                            logger.warning(f"容器 {sandbox_id} 已停止，从数据库删除")
                            self._db.delete_container(sandbox_id)
                            removed_count += 1
                    except Exception as e:
                        logger.error(f"检查容器 {sandbox_id} 状态失败: {e}")
                        removed_count += 1
                else:
                    # 容器不存在，从数据库删除
                    logger.warning(f"容器 {sandbox_id} 不存在，从数据库删除")
                    self._db.delete_container(sandbox_id)
                    removed_count += 1
            
            logger.info(
                f"容器恢复完成: 恢复 {restored_count} 个，清理 {removed_count} 个"
            )
        
        except Exception as e:
            logger.error(f"从数据库恢复容器失败: {e}", exc_info=True)
    
    def get_docker_container(self, sandbox_id: str):
        """获取 Docker 容器对象（用于高级操作）
        
        Args:
            sandbox_id: Sandbox 标识符
            
        Returns:
            Docker Container 对象，如果不存在则返回 None
        """
        container_name = f"sandbox-{sandbox_id}"
        try:
            return self._docker_client.containers.get(container_name)
        except NotFound:
            return None
        except Exception as e:
            logger.error(f"获取 Docker 容器失败: {e}")
            return None
    
    def list_docker_containers(self):
        """列出所有由调度器管理的 Docker 容器"""
        try:
            return self._docker_client.containers.list(
                filters={"label": "sandbox.managed=true"}
            )
        except Exception as e:
            logger.error(f"列出 Docker 容器失败: {e}")
            return []
    
    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        stats = {
            "total_containers": len(self.containers),
            "healthy_containers": sum(
                1 for c in self.containers.values() if c.is_healthy
            ),
            "used_ports": len(self.used_ports),
            "containers": [
                {
                    "sandbox_id": sandbox_id,
                    "port": c.port,
                    "status": c.status.value,
                    "total_requests": c.total_requests,
                    "failed_requests": c.failed_requests,
                    "success_rate": c.success_rate,
                    "last_health_check": (
                        c.last_health_check.isoformat() 
                        if c.last_health_check else None
                    ),
                }
                for sandbox_id, c in self.containers.items()
            ],
        }
        
        # 添加数据库统计信息
        if self._db:
            db_stats = self._db.get_stats()
            stats["database"] = {
                "total_containers": db_stats.get("total_containers", 0),
                "by_status": db_stats.get("by_status", {}),
                "total_sessions": db_stats.get("total_sessions", 0),
            }
        
        return stats
    
    def get_database(self) -> Database | None:
        """获取数据库实例（用于高级操作）"""
        return self._db
    
    async def stop_all_containers(self) -> dict[str, bool]:
        """停止所有容器
        
        此方法会停止并删除所有运行中的容器。
        通常只在需要完全清理时使用。
        
        Returns:
            sandbox_id -> 是否成功停止的字典
        """
        logger.warning(f"准备停止所有 {len(self.containers)} 个容器")
        
        results = {}
        sandbox_ids = list(self.containers.keys())
        
        for sandbox_id in sandbox_ids:
            try:
                success = await self.stop_container(sandbox_id)
                results[sandbox_id] = success
                if success:
                    logger.info(f"已停止容器: {sandbox_id}")
                else:
                    logger.error(f"停止容器失败: {sandbox_id}")
            except Exception as e:
                logger.error(f"停止容器 {sandbox_id} 异常: {e}")
                results[sandbox_id] = False
        
        logger.info(f"容器清理完成: 成功 {sum(results.values())} / 总计 {len(results)}")
        return results

