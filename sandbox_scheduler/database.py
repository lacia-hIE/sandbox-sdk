"""数据库持久化层"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from .types import ContainerInfo, ContainerStatus

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库管理器
    
    负责持久化容器状态信息，包括：
    - 容器基本信息（sandbox_id, port, url, status）
    - 容器统计信息（请求数、失败数等）
    - 容器健康状态
    """
    
    def __init__(self, db_path: str = "scheduler.db"):
        """初始化数据库
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self._ensure_db_directory()
        self._init_db()
    
    def _ensure_db_directory(self) -> None:
        """确保数据库目录存在"""
        db_file = Path(self.db_path)
        if db_file.parent != Path('.'):
            db_file.parent.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接（上下文管理器）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self) -> None:
        """初始化数据库表结构"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 容器信息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS containers (
                    sandbox_id TEXT PRIMARY KEY,
                    port INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_health_check TEXT,
                    last_request_time TEXT NOT NULL,
                    consecutive_failures INTEGER DEFAULT 0,
                    total_requests INTEGER DEFAULT 0,
                    failed_requests INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # 活跃会话表（用于记录容器的活跃会话）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sandbox_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (sandbox_id) REFERENCES containers(sandbox_id) ON DELETE CASCADE,
                    UNIQUE(sandbox_id, session_id)
                )
            """)
            
            # 创建索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_containers_status 
                ON containers(status)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_sandbox 
                ON active_sessions(sandbox_id)
            """)
            
            logger.info(f"数据库初始化完成: {self.db_path}")
    
    def save_container(self, sandbox_id: str, container: ContainerInfo) -> bool:
        """保存或更新容器信息
        
        Args:
            sandbox_id: Sandbox 标识符
            container: 容器信息
            
        Returns:
            是否成功保存
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                
                # 检查是否已存在
                cursor.execute(
                    "SELECT sandbox_id FROM containers WHERE sandbox_id = ?",
                    (sandbox_id,)
                )
                exists = cursor.fetchone() is not None
                
                if exists:
                    # 更新现有记录
                    cursor.execute("""
                        UPDATE containers SET
                            port = ?,
                            url = ?,
                            status = ?,
                            last_health_check = ?,
                            last_request_time = ?,
                            consecutive_failures = ?,
                            total_requests = ?,
                            failed_requests = ?,
                            updated_at = ?
                        WHERE sandbox_id = ?
                    """, (
                        container.port,
                        container.url,
                        container.status.value,
                        container.last_health_check.isoformat() if container.last_health_check else None,
                        container.last_request_time.isoformat(),
                        container.consecutive_failures,
                        container.total_requests,
                        container.failed_requests,
                        now,
                        sandbox_id,
                    ))
                else:
                    # 插入新记录
                    cursor.execute("""
                        INSERT INTO containers (
                            sandbox_id, port, url, status,
                            last_health_check, last_request_time,
                            consecutive_failures, total_requests, failed_requests,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        sandbox_id,
                        container.port,
                        container.url,
                        container.status.value,
                        container.last_health_check.isoformat() if container.last_health_check else None,
                        container.last_request_time.isoformat(),
                        container.consecutive_failures,
                        container.total_requests,
                        container.failed_requests,
                        now,
                        now,
                    ))
                
                # 保存活跃会话
                self._save_sessions(cursor, sandbox_id, container.active_sessions)
                
                logger.debug(f"容器 {sandbox_id} 已保存到数据库")
                return True
        
        except Exception as e:
            logger.error(f"保存容器 {sandbox_id} 失败: {e}", exc_info=True)
            return False
    
    def _save_sessions(
        self, 
        cursor: sqlite3.Cursor, 
        sandbox_id: str, 
        sessions: set[str]
    ) -> None:
        """保存活跃会话
        
        Args:
            cursor: 数据库游标
            sandbox_id: Sandbox 标识符
            sessions: 活跃会话集合
        """
        # 删除旧会话
        cursor.execute(
            "DELETE FROM active_sessions WHERE sandbox_id = ?",
            (sandbox_id,)
        )
        
        # 插入新会话
        now = datetime.now().isoformat()
        for session_id in sessions:
            cursor.execute("""
                INSERT INTO active_sessions (sandbox_id, session_id, created_at)
                VALUES (?, ?, ?)
            """, (sandbox_id, session_id, now))
    
    def load_container(self, sandbox_id: str) -> ContainerInfo | None:
        """加载容器信息
        
        Args:
            sandbox_id: Sandbox 标识符
            
        Returns:
            容器信息，如果不存在则返回 None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 查询容器信息
                cursor.execute(
                    "SELECT * FROM containers WHERE sandbox_id = ?",
                    (sandbox_id,)
                )
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                # 查询活跃会话
                cursor.execute(
                    "SELECT session_id FROM active_sessions WHERE sandbox_id = ?",
                    (sandbox_id,)
                )
                sessions = {row['session_id'] for row in cursor.fetchall()}
                
                # 构造 ContainerInfo 对象
                return self._row_to_container(dict(row), sessions)
        
        except Exception as e:
            logger.error(f"加载容器 {sandbox_id} 失败: {e}", exc_info=True)
            return None
    
    def load_all_containers(self) -> dict[str, ContainerInfo]:
        """加载所有容器信息
        
        Returns:
            sandbox_id -> ContainerInfo 的字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 查询所有容器
                cursor.execute("SELECT * FROM containers")
                rows = cursor.fetchall()
                
                containers = {}
                for row in rows:
                    row_dict = dict(row)
                    sandbox_id = row_dict['sandbox_id']
                    
                    # 查询该容器的活跃会话
                    cursor.execute(
                        "SELECT session_id FROM active_sessions WHERE sandbox_id = ?",
                        (sandbox_id,)
                    )
                    sessions = {r['session_id'] for r in cursor.fetchall()}
                    
                    # 构造 ContainerInfo 对象
                    container = self._row_to_container(row_dict, sessions)
                    containers[sandbox_id] = container
                
                logger.info(f"从数据库加载了 {len(containers)} 个容器")
                return containers
        
        except Exception as e:
            logger.error(f"加载所有容器失败: {e}", exc_info=True)
            return {}
    
    def _row_to_container(
        self, 
        row: dict[str, Any], 
        sessions: set[str]
    ) -> ContainerInfo:
        """将数据库行转换为 ContainerInfo 对象
        
        Args:
            row: 数据库行字典
            sessions: 活跃会话集合
            
        Returns:
            ContainerInfo 对象
        """
        return ContainerInfo(
            port=row['port'],
            url=row['url'],
            status=ContainerStatus(row['status']),
            active_sessions=sessions,
            last_health_check=(
                datetime.fromisoformat(row['last_health_check'])
                if row['last_health_check'] else None
            ),
            last_request_time=datetime.fromisoformat(row['last_request_time']),
            consecutive_failures=row['consecutive_failures'],
            total_requests=row['total_requests'],
            failed_requests=row['failed_requests'],
        )
    
    def delete_container(self, sandbox_id: str) -> bool:
        """删除容器记录
        
        Args:
            sandbox_id: Sandbox 标识符
            
        Returns:
            是否成功删除
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 删除容器（会级联删除相关会话）
                cursor.execute(
                    "DELETE FROM containers WHERE sandbox_id = ?",
                    (sandbox_id,)
                )
                
                logger.debug(f"容器 {sandbox_id} 已从数据库删除")
                return True
        
        except Exception as e:
            logger.error(f"删除容器 {sandbox_id} 失败: {e}", exc_info=True)
            return False
    
    def clear_all(self) -> bool:
        """清空所有数据
        
        Returns:
            是否成功清空
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM active_sessions")
                cursor.execute("DELETE FROM containers")
                logger.info("数据库已清空")
                return True
        
        except Exception as e:
            logger.error(f"清空数据库失败: {e}", exc_info=True)
            return False
    
    def get_stats(self) -> dict[str, Any]:
        """获取数据库统计信息
        
        Returns:
            统计信息字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 容器总数
                cursor.execute("SELECT COUNT(*) as count FROM containers")
                total = cursor.fetchone()['count']
                
                # 各状态容器数
                cursor.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM containers 
                    GROUP BY status
                """)
                by_status = {row['status']: row['count'] for row in cursor.fetchall()}
                
                # 活跃会话总数
                cursor.execute("SELECT COUNT(*) as count FROM active_sessions")
                total_sessions = cursor.fetchone()['count']
                
                return {
                    "total_containers": total,
                    "by_status": by_status,
                    "total_sessions": total_sessions,
                }
        
        except Exception as e:
            logger.error(f"获取数据库统计失败: {e}", exc_info=True)
            return {}
    
    def vacuum(self) -> bool:
        """压缩数据库
        
        Returns:
            是否成功压缩
        """
        try:
            with self._get_connection() as conn:
                conn.execute("VACUUM")
                logger.info("数据库已压缩")
                return True
        
        except Exception as e:
            logger.error(f"压缩数据库失败: {e}", exc_info=True)
            return False

