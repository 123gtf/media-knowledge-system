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

        # 内存模拟（Neo4j不可用时的降级方案）
        self._mem_entities: Dict[str, Dict] = {}    # name -> {type, confidence, mention_count}
        self._mem_relations: List[Dict] = []         # [{head, tail, relation_type, confidence, evidence}]

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

    def _ensure_connected(self) -> bool:
        """触发懒加载，返回连接状态"""
        if self._driver is None:
            _ = self.driver  # 触发 property 懒加载
        return self._connected

    def _execute(self, cypher: str, params: Dict = None) -> List[Dict]:
        """执行Cypher查询"""
        if not self._ensure_connected():
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
        # 始终写入内存
        if name in self._mem_entities:
            ent = self._mem_entities[name]
            ent["mention_count"] = ent.get("mention_count", 0) + 1
            if ent.get("confidence", 0) < confidence:
                ent["confidence"] = confidence
        else:
            self._mem_entities[name] = {
                "name": name,
                "type": entity_type,
                "confidence": confidence,
                "mention_count": 1,
                "aliases": aliases or [],
            }

        # Neo4j 写入
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
        head_type: str = "TOPIC",
        tail_name: str = "",
        tail_type: str = "TOPIC",
        relation_type: str = "related_to",
        confidence: float = 0.8,
        evidence: str = "",
    ) -> bool:
        """创建或更新关系边（按名称匹配实体，不要求类型）"""
        # 始终写入内存
        found = False
        for r in self._mem_relations:
            if r["head"] == head_name and r["tail"] == tail_name and r["relation_type"] == relation_type:
                r["count"] = r.get("count", 1) + 1
                if r.get("confidence", 0) < confidence:
                    r["confidence"] = confidence
                found = True
                break
        if not found:
            self._mem_relations.append({
                "head": head_name,
                "head_type": head_type,
                "tail": tail_name,
                "tail_type": tail_type,
                "relation_type": relation_type,
                "confidence": confidence,
                "evidence": evidence,
                "count": 1,
            })

        # Neo4j 写入
        cypher = """
        MATCH (h:Entity {name: $head_name})
        MATCH (t:Entity {name: $tail_name})
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
            "tail_name": tail_name,
            "relation_type": relation_type,
            "confidence": confidence,
            "evidence": evidence,
        })
        return len(results) > 0 or not found

    def batch_upsert_relations(self, relations: List[Dict]) -> Dict[str, int]:
        """批量更新关系"""
        created = 0
        for r in relations:
            success = self.upsert_relation(
                head_name=r.get("head", ""),
                tail_name=r.get("tail", ""),
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
        # Neo4j 查询
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
        results = self._execute(cypher, {
            "name": name,
            "type": entity_type,
            "limit": limit,
        })

        # 内存 fallback
        if not results and self._mem_entities:
            name_lower = name.lower()
            for ent in self._mem_entities.values():
                ent_name = ent.get("name", "").lower()
                if name_lower in ent_name or ent_name in name_lower:
                    if entity_type and ent.get("type") != entity_type:
                        continue
                    results.append({
                        "name": ent["name"],
                        "type": ent.get("type", ""),
                        "confidence": ent.get("confidence", 0),
                        "mention_count": ent.get("mention_count", 0),
                    })
            results.sort(key=lambda x: x.get("mention_count", 0), reverse=True)
            results = results[:limit]

        return results

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

        # 内存 fallback
        if not results and self._mem_relations:
            for rel in self._mem_relations:
                if rel["head"] == entity_name or rel["tail"] == entity_name:
                    other = rel["tail"] if rel["head"] == entity_name else rel["head"]
                    nodes.add((other, rel.get("tail_type", rel.get("head_type", ""))))
                    edges.append({
                        "relation_type": rel.get("relation_type", "related_to"),
                        "confidence": rel.get("confidence", 0.8),
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

    def load_demo_data(self):
        """加载演示数据到图谱（用于对话模式）"""
        if self._mem_entities:
            return  # 已有数据，跳过

        # 实体数据
        entities = [
            {"name": "OpenAI", "type": "ORG", "confidence": 0.95},
            {"name": "GPT-5", "type": "TOPIC", "confidence": 0.95},
            {"name": "Sam Altman", "type": "PER", "confidence": 0.95},
            {"name": "微软", "type": "ORG", "confidence": 0.9},
            {"name": "Google DeepMind", "type": "ORG", "confidence": 0.9},
            {"name": "Demis Hassabis", "type": "PER", "confidence": 0.9},
            {"name": "阿里巴巴", "type": "ORG", "confidence": 0.95},
            {"name": "上海", "type": "LOC", "confidence": 0.9},
            {"name": "吴泳铭", "type": "PER", "confidence": 0.9},
            {"name": "腾讯", "type": "ORG", "confidence": 0.9},
            {"name": "百度", "type": "ORG", "confidence": 0.9},
            {"name": "字节跳动", "type": "ORG", "confidence": 0.95},
            {"name": "豆包2.0", "type": "TOPIC", "confidence": 0.95},
            {"name": "梁汝波", "type": "PER", "confidence": 0.9},
            {"name": "混元大模型", "type": "TOPIC", "confidence": 0.9},
            {"name": "文心一言", "type": "TOPIC", "confidence": 0.9},
            {"name": "商汤科技", "type": "ORG", "confidence": 0.85},
            {"name": "昆仑万维", "type": "ORG", "confidence": 0.85},
            {"name": "Azure", "type": "TOPIC", "confidence": 0.85},
            {"name": "Gemini", "type": "TOPIC", "confidence": 0.85},
        ]
        for e in entities:
            self.upsert_entity(e["name"], e["type"], e["confidence"])

        # 关系数据
        relations = [
            {"head": "Sam Altman", "tail": "OpenAI", "relation_type": "works_for", "confidence": 0.95, "evidence": "OpenAI CEO"},
            {"head": "OpenAI", "tail": "GPT-5", "relation_type": "release", "confidence": 0.95, "evidence": "发布GPT-5"},
            {"head": "微软", "tail": "OpenAI", "relation_type": "invests_in", "confidence": 0.9, "evidence": "最大投资方"},
            {"head": "微软", "tail": "Azure", "relation_type": "related_to", "confidence": 0.9, "evidence": "Azure云平台"},
            {"head": "Demis Hassabis", "tail": "Google DeepMind", "relation_type": "works_for", "confidence": 0.9, "evidence": "首席执行官"},
            {"head": "Google DeepMind", "tail": "Gemini", "relation_type": "release", "confidence": 0.85, "evidence": "开发Gemini模型"},
            {"head": "阿里巴巴", "tail": "上海", "relation_type": "located_in", "confidence": 0.9, "evidence": "上海AI研究院"},
            {"head": "吴泳铭", "tail": "阿里巴巴", "relation_type": "works_for", "confidence": 0.9, "evidence": "阿里巴巴CEO"},
            {"head": "腾讯", "tail": "混元大模型", "relation_type": "release", "confidence": 0.9, "evidence": "混元大模型"},
            {"head": "百度", "tail": "文心一言", "relation_type": "release", "confidence": 0.9, "evidence": "文心一言4.0"},
            {"head": "字节跳动", "tail": "豆包2.0", "relation_type": "release", "confidence": 0.95, "evidence": "推出豆包2.0"},
            {"head": "梁汝波", "tail": "字节跳动", "relation_type": "works_for", "confidence": 0.9, "evidence": "字节跳动CEO"},
            {"head": "商汤科技", "tail": "豆包2.0", "relation_type": "related_to", "confidence": 0.7, "evidence": "AI助手竞争"},
            {"head": "昆仑万维", "tail": "豆包2.0", "relation_type": "related_to", "confidence": 0.7, "evidence": "AI助手竞争"},
        ]
        for r in relations:
            self.upsert_relation(
                head_name=r["head"],
                tail_name=r["tail"],
                relation_type=r["relation_type"],
                confidence=r["confidence"],
                evidence=r["evidence"],
            )

        logger.info(f"演示数据已加载: {len(self._mem_entities)} 实体, {len(self._mem_relations)} 关系")

    def close(self):
        """关闭连接"""
        if self._driver:
            self._driver.close()
            self._driver = None
            self._connected = False
