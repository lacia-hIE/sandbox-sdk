"""调度服务类型定义"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ContainerStatus(str, Enum):
    """容器状态"""
    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass
class ContainerInfo:
    """容器信息"""
    port: int
    url: str
    status: ContainerStatus = ContainerStatus.INITIALIZING
    active_sessions: set[str] = field(default_factory=set)
    last_health_check: datetime | None = None
    last_request_time: datetime = field(default_factory=datetime.now)
    consecutive_failures: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    
    @property
    def is_healthy(self) -> bool:
        """是否健康"""
        return self.status == ContainerStatus.HEALTHY
    
    @property
    def session_count(self) -> int:
        """活跃会话数"""
        return len(self.active_sessions)
    
    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_requests == 0:
            return 1.0
        return (self.total_requests - self.failed_requests) / self.total_requests
    
    def can_accept_session(self, max_sessions: int) -> bool:
        """是否可以接受新会话"""
        return self.is_healthy and self.session_count < max_sessions


@dataclass
class RouteResult:
    """路由结果"""
    container: ContainerInfo
    is_new_session: bool = False


@dataclass
class HealthCheckResult:
    """健康检查结果"""
    port: int
    is_healthy: bool
    response_time: float  # 毫秒
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

