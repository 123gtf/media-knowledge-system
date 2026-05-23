"""
MySQL 数据访问层

封装对以下表的CRUD操作：
- raw_articles: 原始媒体数据
- cleaned_articles: 清洗后数据
- entities: 实体索引
- relations: 关系记录
- agent_execution_log: Agent执行日志
- analysis_reports: 分析报告归档
- quality_reviews: 质检记录
- task_blackboard: 任务黑板
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MySQLRepository:
    """MySQL数据访问层"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "media_knowledge_db",
        charset: str = "utf8mb4",
    ):
        self.config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": charset,
        }
        self._engine = None
        self._connected = False

    @property
    def engine(self):
        """懒加载SQLAlchemy引擎"""
        if self._engine is None:
            try:
                from sqlalchemy import create_engine
                url = (
                    f"mysql+pymysql://{self.config['user']}:{self.config['password']}"
                    f"@{self.config['host']}:{self.config['port']}/{self.config['database']}"
                    f"?charset={self.config['charset']}"
                )
                self._engine = create_engine(url, pool_size=5, pool_recycle=3600)
                self._connected = True
                logger.info(f"MySQL连接成功: {self.config['host']}:{self.config['port']}")
            except ImportError:
                logger.warning("pymysql/sqlalchemy未安装，使用内存模拟")
                self._connected = False
            except Exception as e:
                logger.warning(f"MySQL连接失败: {e}，使用内存模拟")
                self._connected = False
        return self._engine

    @contextmanager
    def _get_connection(self):
        """获取数据库连接上下文"""
        if not self._connected:
            yield None
            return

        try:
            with self.engine.connect() as conn:
                yield conn
        except Exception as e:
            logger.error(f"数据库操作失败: {e}")
            yield None

    # ========================================
    # 实体操作
    # ========================================

    def find_entity_by_name_type(self, name: str, entity_type: str) -> Optional[Dict]:
        """按名称和类型精确查找实体"""
        if not self._connected:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                result = conn.execute(
                    text(
                        "SELECT id, name, entity_type, confidence, aliases, mention_count "
                        "FROM entities WHERE LOWER(name) = LOWER(:name) AND entity_type = :type "
                        "LIMIT 1"
                    ),
                    {"name": name, "type": entity_type},
                )
                row = result.fetchone()
                if row:
                    return dict(row._mapping)
        except Exception as e:
            logger.warning(f"实体查询失败: {e}")
        return None

    def find_similar_entities(self, name: str, entity_type: str, limit: int = 10) -> List[Dict]:
        """模糊查找相似实体"""
        if not self._connected:
            return []

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []
                from sqlalchemy import text
                result = conn.execute(
                    text(
                        "SELECT id, name, entity_type, confidence, aliases, mention_count "
                        "FROM entities "
                        "WHERE (LOWER(name) LIKE CONCAT('%', LOWER(:name), '%') "
                        "   OR LOWER(:name) LIKE CONCAT('%', LOWER(name), '%')) "
                        "  AND entity_type = :type "
                        "ORDER BY mention_count DESC "
                        "LIMIT :limit"
                    ),
                    {"name": name, "type": entity_type, "limit": limit},
                )
                return [dict(row._mapping) for row in result.fetchall()]
        except Exception as e:
            logger.warning(f"实体模糊查找失败: {e}")
            return []

    def upsert_entity(self, entity: Dict[str, Any]) -> Optional[int]:
        """插入或更新实体 (ON DUPLICATE KEY UPDATE)，返回实体ID"""
        if not self._connected:
            return None

        entity_name = entity.get("name", "")
        entity_type = entity.get("type", entity.get("entity_type", "TOPIC"))

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                import json as json_module
                conn.execute(
                    text(
                        "INSERT INTO entities (name, entity_type, aliases, confidence, mention_count) "
                        "VALUES (:name, :type, :aliases, :confidence, 1) "
                        "ON DUPLICATE KEY UPDATE "
                        "  confidence = GREATEST(confidence, :confidence2), "
                        "  mention_count = mention_count + 1, "
                        "  last_seen = NOW()"
                    ),
                    {
                        "name": entity_name,
                        "type": entity_type,
                        "aliases": json_module.dumps(entity.get("aliases", [entity_name])),
                        "confidence": entity.get("confidence", 0.8),
                        "confidence2": entity.get("confidence", 0.8),
                    },
                )
                conn.commit()
                # 查询获取真实ID（ON DUPLICATE KEY UPDATE 时 lastrowid 不可靠）
                result = conn.execute(
                    text("SELECT id FROM entities WHERE name = :name AND entity_type = :type LIMIT 1"),
                    {"name": entity_name, "type": entity_type},
                )
                row = result.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.warning(f"实体upsert失败 [{entity_name}]: {e}")
            return None

    def get_entity_id(self, name: str, entity_type: str) -> Optional[int]:
        """按名称和类型获取实体ID"""
        if not self._connected:
            return None
        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                result = conn.execute(
                    text("SELECT id FROM entities WHERE name = :name AND entity_type = :type LIMIT 1"),
                    {"name": name, "type": entity_type},
                )
                row = result.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    # ========================================
    # 关系操作
    # ========================================

    def insert_relation(self, relation: Dict[str, Any]) -> Optional[int]:
        """插入关系记录"""
        if not self._connected:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                result = conn.execute(
                    text(
                        "INSERT INTO relations (head_entity_id, tail_entity_id, relation_type, confidence, evidence) "
                        "VALUES (:head_id, :tail_id, :rel_type, :confidence, :evidence)"
                    ),
                    {
                        "head_id": relation.get("head_id", 0),
                        "tail_id": relation.get("tail_id", 0),
                        "rel_type": relation.get("relation", relation.get("relation_type", "related_to")),
                        "confidence": relation.get("confidence", 0.8),
                        "evidence": relation.get("evidence", ""),
                    },
                )
                conn.commit()
                return result.lastrowid
        except Exception as e:
            logger.warning(f"关系插入失败: {e}")
            return None

    def get_recent_relations(self, limit: int = 500) -> List[Dict]:
        """获取最近的关关系记录"""
        if not self._connected:
            return []

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return []
                from sqlalchemy import text
                result = conn.execute(
                    text(
                        "SELECT r.id, e1.name as head, e2.name as tail, "
                        "r.relation_type as relation, r.confidence "
                        "FROM relations r "
                        "JOIN entities e1 ON r.head_entity_id = e1.id "
                        "JOIN entities e2 ON r.tail_entity_id = e2.id "
                        "ORDER BY r.created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                )
                return [dict(row._mapping) for row in result.fetchall()]
        except Exception as e:
            logger.warning(f"关系查询失败: {e}")
            return []

    # ========================================
    # 日志操作
    # ========================================

    def log_agent_action(
        self,
        task_id: str,
        agent_type: str,
        action: str,
        status: str = "RUNNING",
        result: Dict = None,
        duration_ms: int = 0,
    ) -> Optional[int]:
        """记录Agent执行日志"""
        if not self._connected:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                import json as json_module
                r = conn.execute(
                    text(
                        "INSERT INTO agent_execution_log (task_id, agent_type, action, status, result, duration_ms) "
                        "VALUES (:task_id, :agent_type, :action, :status, :result, :duration_ms)"
                    ),
                    {
                        "task_id": task_id,
                        "agent_type": agent_type,
                        "action": action,
                        "status": status,
                        "result": json_module.dumps(result) if result else None,
                        "duration_ms": duration_ms,
                    },
                )
                conn.commit()
                return r.lastrowid
        except Exception as e:
            logger.debug(f"日志写入失败: {e}")
            return None

    # ========================================
    # 报告操作
    # ========================================

    def save_report(
        self,
        report_id: str,
        task_id: str,
        title: str,
        report_type: str,
        markdown_content: str,
        json_content: Dict = None,
    ) -> Optional[int]:
        """保存分析报告"""
        if not self._connected:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                import json as json_module
                r = conn.execute(
                    text(
                        "INSERT INTO analysis_reports (report_id, task_id, report_title, report_type, "
                        "markdown_content, json_content) "
                        "VALUES (:report_id, :task_id, :title, :type, :md, :json)"
                    ),
                    {
                        "report_id": report_id,
                        "task_id": task_id,
                        "title": title,
                        "type": report_type,
                        "md": markdown_content,
                        "json": json_module.dumps(json_content) if json_content else None,
                    },
                )
                conn.commit()
                return r.lastrowid
        except Exception as e:
            logger.warning(f"报告保存失败: {e}")
            return None

    # ========================================
    # 质检操作
    # ========================================

    def save_quality_review(
        self,
        task_id: str,
        review_type: str,
        passed: bool,
        issues: List[Dict] = None,
        confidence: float = 0.0,
    ) -> Optional[int]:
        """保存质检记录"""
        if not self._connected:
            return None

        try:
            with self._get_connection() as conn:
                if conn is None:
                    return None
                from sqlalchemy import text
                import json as json_module
                r = conn.execute(
                    text(
                        "INSERT INTO quality_reviews (task_id, review_type, passed, issues, reviewer_confidence) "
                        "VALUES (:task_id, :review_type, :passed, :issues, :confidence)"
                    ),
                    {
                        "task_id": task_id,
                        "review_type": review_type,
                        "passed": passed,
                        "issues": json_module.dumps(issues) if issues else None,
                        "confidence": confidence,
                    },
                )
                conn.commit()
                return r.lastrowid
        except Exception as e:
            logger.warning(f"质检记录保存失败: {e}")
            return None
