"""
Neo4j 知识图谱操作封装

提供：
- 实体节点 CRUD (MERGE/CREATE/MATCH)
- 关系边 CRUD
- 子图检索 (多跳邻域)
- 批量操作优化
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphStore:
    """Neo4j 图谱存储操作"""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver = None
        self._connected = False

    @property
    def driver(self):
        """懒加载Neo4j驱动"""
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password),
                )
                self._driver.verify_connectivity()
                self._connected = True
                logger.info(f"Neo4j连接成功: {self.uri}")
            except ImportError:
                logger.warning("neo4j驱动未安装，使用内存模拟")
                self._connected = False
            except Exception as e:
                logger.warning(f"Neo4j连接失败: {e}，使用内存模拟")
                self._connected = False
        return self._driver

    def _execute(self, cypher: str, params: Dict = None) -> List[Dict]:
        """执行Cypher查询"""
        if not self._connected or self._driver is None:
            logger.debug(f"[Mock] Cypher: {cypher[:100]}...")
            return []

        try:
            with self._driver.session(database=self.database) as session:
                result = session.run(cypher, params or {})
                return [record.data() for record in result]
        except Exception as e:
            logger.error(f"Cypher执行失败: {e}")
            return []

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        confidence: float = 0.9,
        aliases: List[str] = None,
    ) -> Optional[int]:
        """创建或更新实体节点 (MERGE语义)"""
        cypher = """
        MERGE (e:Entity {name: $name, type: $type})
        ON CREATE SET
            e.neo4j_id = randomUUID(),
            e.first_seen = datetime(),
            e.confidence = $confidence,
            e.aliases = $aliases,
            e.mention_count = 1
        ON MATCH SET
            e.confidence = CASE WHEN e.confidence < $confidence THEN $confidence ELSE e.confidence END,
            e.mention_count = e.mention_count + 1,
            e.last_seen = datetime()
        RETURN e.neo4j_id as neo4j_id, e.mention_count as mention_count
        """
        results = self._execute(cypher, {
            "name": name,
            "type": entity_type,
            "confidence": confidence,
            "aliases": aliases or [],
        })
        if results:
            return results[0].get("neo4j_id")
        return None

    def batch_upsert_entities(self, entities: List[Dict]) -> Dict[str, int]:
        """批量更新实体"""
        created = 0
        for e in entities:
            neo4j_id = self.upsert_entity(
                name=e.get("name", ""),
                entity_type=e.get("type", "TOPIC"),
                confidence=e.get("confidence", 0.8),
                aliases=e.get("aliases", [e.get("name", "")]),
            )
            if neo4j_id:
                created += 1

        logger.info(f"图谱实体批量写入: {created}/{len(entities)}")
        return {"created": created, "total": len(entities)}

    def upsert_relation(
        self,
        head_name: str,
        head_type: str,
        tail_name: str,
        tail_type: str,
        relation_type: str,
        confidence: float = 0.8,
        evidence: str = "",
    ) -> bool:
        """创建或更新关系边"""
        cypher = """
        MATCH (h:Entity {name: $head_name, type: $head_type})
        MATCH (t:Entity {name: $tail_name, type: $tail_type})
        MERGE (h)-[r:RELATED_TO {type: $relation_type}]->(t)
        ON CREATE SET
            r.confidence = $confidence,
            r.evidence = $evidence,
            r.created_at = datetime(),
            r.count = 1
        ON MATCH SET
            r.count = r.count + 1,
            r.confidence = CASE WHEN r.confidence < $confidence THEN $confidence ELSE r.confidence END,
            r.updated_at = datetime()
        RETURN r
        """
        results = self._execute(cypher, {
            "head_name": head_name,
            "head_type": head_type,
            "tail_name": tail_name,
            "tail_type": tail_type,
            "relation_type": relation_type,
            "confidence": confidence,
            "evidence": evidence,
        })
        return len(results) > 0

    def batch_upsert_relations(self, relations: List[Dict]) -> Dict[str, int]:
        """批量更新关系"""
        created = 0
        for r in relations:
            success = self.upsert_relation(
                head_name=r.get("head", ""),
                head_type=r.get("head_type", "TOPIC"),
                tail_name=r.get("tail", ""),
                tail_type=r.get("tail_type", "TOPIC"),
                relation_type=r.get("relation", r.get("relation_type", "related_to")),
                confidence=r.get("confidence", 0.8),
                evidence=r.get("evidence", ""),
            )
            if success:
                created += 1

        logger.info(f"图谱关系批量写入: {created}/{len(relations)}")
        return {"created": created, "total": len(relations)}

    def find_similar_entities(
        self,
        name: str,
        entity_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """查找名称相似的实体（向量检索的简化版：名称模糊匹配）"""
        cypher = """
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower($name)
           OR toLower($name) CONTAINS toLower(e.name)
        """
        if entity_type:
            cypher += " AND e.type = $type"

        cypher += """
        RETURN e.name as name, e.type as type, e.confidence as confidence,
               e.mention_count as mention_count
        ORDER BY e.mention_count DESC
        LIMIT $limit
        """
        return self._execute(cypher, {
            "name": name,
            "type": entity_type,
            "limit": limit,
        })

    def get_subgraph(self, entity_name: str, depth: int = 2) -> Dict[str, Any]:
        """获取实体的邻域子图"""
        cypher = """
        MATCH (e:Entity {name: $name})-[r:RELATED_TO*1..$depth]-(related:Entity)
        RETURN e, r, related
        LIMIT 100
        """
        results = self._execute(cypher, {"name": entity_name, "depth": depth})

        nodes = set()
        edges = []
        for record in results:
            e_data = record.get("e", {})
            related_data = record.get("related", {})
            if e_data.get("name"):
                nodes.add((e_data["name"], e_data.get("type", "")))
            if related_data.get("name"):
                nodes.add((related_data["name"], related_data.get("type", "")))

            rels = record.get("r", [])
            if isinstance(rels, list):
                for rel in rels:
                    edges.append({
                        "relation_type": rel.get("type", "related_to"),
                        "confidence": rel.get("confidence", 0.8),
                    })
            elif isinstance(rels, dict):
                edges.append({
                    "relation_type": rels.get("type", "related_to"),
                    "confidence": rels.get("confidence", 0.8),
                })

        return {
            "nodes": [{"name": n, "type": t} for n, t in nodes],
            "edges": edges,
        }

    def get_hot_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """获取热点实体（按提及次数排序）"""
        cypher = """
        MATCH (e:Entity)
        """
        if entity_type:
            cypher += "WHERE e.type = $type\n"

        cypher += """
        RETURN e.name as name, e.type as type, e.mention_count as mention_count,
               e.confidence as confidence
        ORDER BY e.mention_count DESC
        LIMIT $limit
        """
        return self._execute(cypher, {"type": entity_type, "limit": limit})

    def get_entity_timeline(self, entity_name: str) -> List[Dict]:
        """获取实体关联的事件时间线"""
        cypher = """
        MATCH (e:Entity {name: $name})-[r:RELATED_TO]-(related:Entity)
        WHERE related.type = 'EVENT' OR related.type = 'TIME'
        RETURN related.name as event, related.type as type,
               r.type as relation_type, r.confidence as confidence
        ORDER BY related.name
        LIMIT 50
        """
        return self._execute(cypher, {"name": entity_name})

    def close(self):
        """关闭连接"""
        if self._driver:
            self._driver.close()
            self._driver = None
            self._connected = False
