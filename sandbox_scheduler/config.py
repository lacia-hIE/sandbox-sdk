"""调度服务配置"""
from dataclasses import dataclass
from typing import Literal


@dataclass
class SchedulerConfig:
    """调度器配置"""
    
    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    
    # 容器配置
    container_base_port: int = 10000
    container_host: str = "localhost"
    container_image: str = "agent-loop-cf_sandbox:latest"  # Docker 镜像名称
    
    # 容器生命周期配置
    container_idle_timeout: int = 3600  # 容器空闲超时（秒），默认 1 小时
    auto_cleanup_enabled: bool = True  # 是否自动清理空闲容器
    max_containers: int = 100  # 最大容器数量限制
    
    # 健康检查配置
    health_check_enabled: bool = True
    health_check_interval: int = 30  # 秒
    health_check_timeout: int = 5  # 秒
    health_check_retries: int = 3
    
    # 请求配置
    request_timeout: int = 60  # 秒
    
    # 容器启动配置
    container_startup_timeout: int = 60  # 秒
    
    # 持久化配置
    persistence_enabled: bool = True  # 是否启用持久化
    database_path: str = "scheduler.db"  # 数据库文件路径
    auto_restore: bool = True  # 是否在启动时自动恢复容器状态
    stop_containers_on_shutdown: bool = False  # 是否在关闭时停止所有容器
    
    def get_container_url(self, port: int) -> str:
        """获取容器 URL"""
        return f"http://{self.container_host}:{port}"

