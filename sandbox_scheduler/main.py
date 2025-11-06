"""调度服务主入口"""
import argparse
import logging
import sys

import uvicorn

from .config import SchedulerConfig
from .server_dynamic import create_app


def setup_logging(level: str = "INFO"):
    """设置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Sandbox Scheduler - 调度和管理多个 sandbox-container 实例"
    )
    
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="服务监听地址（默认: 0.0.0.0）",
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="服务监听端口（默认: 8000）",
    )
    
    parser.add_argument(
        "--container-base-port",
        type=int,
        default=10000,
        help="容器起始端口（默认: 10000）",
    )
    
    parser.add_argument(
        "--container-host",
        default="localhost",
        help="容器主机地址（默认: localhost）",
    )
    
    parser.add_argument(
        "--container-image",
        default="agent-loop-cf_sandbox:latest",
        help="Docker 容器镜像名称（默认: agent-loop-cf_sandbox:latest）",
    )
    
    parser.add_argument(
        "--max-containers",
        type=int,
        default=100,
        help="最大容器数量限制（默认: 100）",
    )
    
    parser.add_argument(
        "--container-idle-timeout",
        type=int,
        default=3600,
        help="容器空闲超时秒数（默认: 3600，即 1 小时）",
    )
    
    parser.add_argument(
        "--health-check-interval",
        type=int,
        default=30,
        help="健康检查间隔秒数（默认: 30）",
    )
    
    parser.add_argument(
        "--no-auto-cleanup",
        action="store_true",
        help="禁用自动清理空闲容器",
    )
    
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别（默认: INFO）",
    )
    
    parser.add_argument(
        "--no-health-check",
        action="store_true",
        help="禁用健康检查",
    )
    
    parser.add_argument(
        "--no-persistence",
        action="store_true",
        help="禁用持久化",
    )
    
    parser.add_argument(
        "--database-path",
        default="scheduler.db",
        help="数据库文件路径（默认: scheduler.db）",
    )
    
    parser.add_argument(
        "--no-auto-restore",
        action="store_true",
        help="禁用启动时自动恢复容器状态",
    )
    
    parser.add_argument(
        "--stop-containers-on-shutdown",
        action="store_true",
        help="服务关闭时停止所有容器（默认保留容器继续运行）",
    )
    
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    
    # 设置日志
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    # 创建配置
    config = SchedulerConfig(
        host=args.host,
        port=args.port,
        container_base_port=args.container_base_port,
        container_host=args.container_host,
        container_image=args.container_image,
        max_containers=args.max_containers,
        container_idle_timeout=args.container_idle_timeout,
        auto_cleanup_enabled=not args.no_auto_cleanup,
        health_check_enabled=not args.no_health_check,
        health_check_interval=args.health_check_interval,
        persistence_enabled=not args.no_persistence,
        database_path=args.database_path,
        auto_restore=not args.no_auto_restore,
        stop_containers_on_shutdown=args.stop_containers_on_shutdown,
    )
    
    logger.info("=" * 60)
    logger.info("Sandbox Scheduler (Dynamic) 启动配置:")
    logger.info(f"  服务地址: {config.host}:{config.port}")
    logger.info(f"  容器镜像: {config.container_image}")
    logger.info(f"  容器起始端口: {config.container_base_port}")
    logger.info(f"  容器主机: {config.container_host}")
    logger.info(f"  最大容器数: {config.max_containers}")
    logger.info(f"  容器空闲超时: {config.container_idle_timeout}秒")
    logger.info(f"  自动清理: {'启用' if config.auto_cleanup_enabled else '禁用'}")
    logger.info(f"  健康检查: {'启用' if config.health_check_enabled else '禁用'}")
    logger.info(f"  持久化: {'启用' if config.persistence_enabled else '禁用'}")
    if config.persistence_enabled:
        logger.info(f"  数据库路径: {config.database_path}")
        logger.info(f"  自动恢复: {'启用' if config.auto_restore else '禁用'}")
    logger.info(f"  关闭时停止容器: {'是' if config.stop_containers_on_shutdown else '否'}")
    logger.info("=" * 60)
    
    # 创建应用
    app = create_app(config)
    
    # 启动服务
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()

