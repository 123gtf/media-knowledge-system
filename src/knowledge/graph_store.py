"""
Neo4j 知识图谱存储（含去重）

提供：
- 实体节点 CRUD (MERGE/CREATE/MATCH)
- 关系边 CRUD
- 别名映射 + 规范化名称去重
- 子图检索 (多跳邻域)
- 批量操作优化
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphStore:
    """Neo4j 图谱存储操作（含去重）"""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
        memory_only: bool = False,
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver = None
        self._connected = False
        self._memory_only = memory_only

        # 内存存储
        self._mem_entities: Dict[str, Dict] = {}    # name -> {type, confidence, mention_count, aliases}
        self._mem_relations: List[Dict] = []

        # ===== 去重核心 =====
        # 别名映射：别名(canonical) -> 规范名
        # 例: {"阿里": "阿里巴巴", "openai": "OpenAI", "字节": "字节跳动"}
        self._aliases: Dict[str, str] = {}
        # 规范名索引：小写名称 -> 规范名（用于大小写去重）
        # 例: {"openai": "OpenAI", "阿里巴巴": "阿里巴巴"}
        self._name_index: Dict[str, str] = {}

    # ==================================================================
    # 去重方法
    # ==================================================================

    def register_alias(self, alias: str, canonical_name: str):
        """注册别名映射（小写 -> 规范名）"""
        self._aliases[alias.lower()] = canonical_name
        # 同时建立小写索引
        self._name_index[canonical_name.lower()] = canonical_name

    def register_aliases(self, mapping: Dict[str, str]):
        """批量注册别名映射"""
        for alias, canonical in mapping.items():
            self.register_alias(alias, canonical)

    def _normalize_name(self, name: str) -> str:
        """名称规范化：去首尾空格，统一全半角"""
        return name.strip()

    def _resolve_entity(self, name: str) -> Optional[str]:
        """
        将输入名称解析为图谱中已存在的规范名

        查找顺序：
        1. 精确匹配（已有 name）
        2. 别名映射匹配
        3. 小写不区分匹配
        4. 子串包含匹配（输入包含已有，或已有包含输入）

        Returns:
            规范名 or None（未找到匹配）
        """
        name_norm = self._normalize_name(name)

        # 1. 精确匹配
        if name_norm in self._mem_entities:
            return name_norm

        # 2. 别名映射
        alias_target = self._aliases.get(name_norm.lower())
        if alias_target and alias_target in self._mem_entities:
            return alias_target

        # 3. 小写不区分匹配
        name_lower = name_norm.lower()
        canonical = self._name_index.get(name_lower)
        if canonical and canonical in self._mem_entities:
            return canonical

        # 4. 子串包含匹配（宽松，仅在无精确/别名匹配时启用）
        for existing_name in self._mem_entities:
            el = existing_name.lower()
            if name_lower == el:
                return existing_name
            # 包含关系：且两者长度差距不大（避免 "ai" 匹配 "AI芯片"）
            if (name_lower in el or el in name_lower) and abs(len(name_lower) - len(el)) <= 3:
                return existing_name

        return None

    def _resolve_relation_exists(
        self, head_canonical: str, tail_canonical: str, relation_type: str
    ) -> bool:
        """检查关系是否已存在（按规范名）"""
        for r in self._mem_relations:
            if (r["head"] == head_canonical
                    and r["tail"] == tail_canonical
                    and r["relation_type"] == relation_type):
                return True
        return False

    # ==================================================================
    # Neo4j 连接
    # ==================================================================

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
        if self._memory_only:
            return False
        if self._driver is None:
            _ = self.driver
        return self._connected

    def _execute(self, cypher: str, params: Dict = None) -> List[Dict]:
        """执行Cypher查询"""
        if not self._ensure_connected():
            return []

        try:
            with self._driver.session(database=self.database) as session:
                result = session.run(cypher, params or {})
                return [record.data() for record in result]
        except Exception as e:
            logger.error(f"Cypher执行失败: {e}")
            return []

    # ==================================================================
    # 实体 CRUD
    # ==================================================================

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        confidence: float = 0.9,
        aliases: List[str] = None,
    ) -> Optional[int]:
        """创建或更新实体节点（自动去重）"""
        name_norm = self._normalize_name(name)

        # ---- 去重：先尝试解析已有实体 ----
        existing = self._resolve_entity(name_norm)
        if existing:
            ent = self._mem_entities[existing]
            ent["mention_count"] = ent.get("mention_count", 0) + 1
            if ent.get("confidence", 0) < confidence:
                ent["confidence"] = confidence
            # 将本次传入的 name 注册为别名（如果不是规范名本身）
            if name_norm.lower() != existing.lower():
                self.register_alias(name_norm, existing)
            # 合并传入的 aliases
            for a in (aliases or []):
                if a.lower() != existing.lower():
                    self.register_alias(a, existing)
                    if a not in ent.get("aliases", []):
                        ent.setdefault("aliases", []).append(a)
            # 同步 Neo4j
            return self._neo4j_upsert_entity(existing, ent.get("type", entity_type), ent["confidence"], ent.get("aliases", []))

        # ---- 新实体 ----
        self._mem_entities[name_norm] = {
            "name": name_norm,
            "type": entity_type,
            "confidence": confidence,
            "mention_count": 1,
            "aliases": aliases or [],
        }
        # 建立小写索引
        self._name_index[name_norm.lower()] = name_norm
        # 注册传入的 aliases
        for a in (aliases or []):
            self.register_alias(a, name_norm)

        # 同步 Neo4j
        return self._neo4j_upsert_entity(name_norm, entity_type, confidence, aliases or [])

    def _neo4j_upsert_entity(
        self, name: str, entity_type: str, confidence: float, aliases: List[str]
    ) -> Optional[int]:
        """Neo4j 实体写入"""
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
            "aliases": aliases,
        })
        if results:
            return results[0].get("neo4j_id")
        return None

    def batch_upsert_entities(self, entities: List[Dict]) -> Dict[str, int]:
        """批量更新实体（自动去重）"""
        created = 0
        skipped = 0
        for e in entities:
            self.upsert_entity(
                name=e.get("name", ""),
                entity_type=e.get("type", "TOPIC"),
                confidence=e.get("confidence", 0.8),
                aliases=e.get("aliases", [e.get("name", "")]),
            )
            # 通过 mention_count 判断是否为新实体
            name = self._normalize_name(e.get("name", ""))
            resolved = self._resolve_entity(name)
            if resolved and self._mem_entities[resolved].get("mention_count", 0) == 1:
                created += 1
            else:
                skipped += 1

        logger.info(f"图谱实体批量写入: 新增{created} / 去重跳过{skipped} / 总计{len(entities)}")
        return {"created": created, "skipped": skipped, "total": len(entities)}

    # ==================================================================
    # 关系 CRUD
    # ==================================================================

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
        """创建或更新关系边（自动去重，实体名先解析为规范名）"""
        # 解析实体规范名
        head_canonical = self._resolve_entity(head_name) or head_name
        tail_canonical = self._resolve_entity(tail_name) or tail_name

        # 检查关系是否已存在
        if self._resolve_relation_exists(head_canonical, tail_canonical, relation_type):
            for r in self._mem_relations:
                if (r["head"] == head_canonical
                        and r["tail"] == tail_canonical
                        and r["relation_type"] == relation_type):
                    r["count"] = r.get("count", 1) + 1
                    if r.get("confidence", 0) < confidence:
                        r["confidence"] = confidence
                    break
            return True  # 已存在，更新计数

        # 新关系
        self._mem_relations.append({
            "head": head_canonical,
            "head_type": head_type,
            "tail": tail_canonical,
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
        self._execute(cypher, {
            "head_name": head_canonical,
            "tail_name": tail_canonical,
            "relation_type": relation_type,
            "confidence": confidence,
            "evidence": evidence,
        })
        return True

    def batch_upsert_relations(self, relations: List[Dict]) -> Dict[str, int]:
        """批量更新关系（自动去重）"""
        created = 0
        skipped = 0
        for r in relations:
            before = len(self._mem_relations)
            self.upsert_relation(
                head_name=r.get("head", ""),
                tail_name=r.get("tail", ""),
                relation_type=r.get("relation", r.get("relation_type", "related_to")),
                confidence=r.get("confidence", 0.8),
                evidence=r.get("evidence", ""),
            )
            if len(self._mem_relations) > before:
                created += 1
            else:
                skipped += 1

        logger.info(f"图谱关系批量写入: 新增{created} / 去重跳过{skipped} / 总计{len(relations)}")
        return {"created": created, "skipped": skipped, "total": len(relations)}

    # ==================================================================
    # 查询
    # ==================================================================

    def find_similar_entities(
        self,
        name: str,
        entity_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """查找名称相似的实体（别名匹配 + 模糊匹配）"""
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

        # 内存 fallback（含别名匹配）
        if not results and self._mem_entities:
            seen = set()

            # 1. 先查别名映射
            alias_target = self._aliases.get(name.lower())
            if alias_target and alias_target in self._mem_entities:
                ent = self._mem_entities[alias_target]
                if not entity_type or ent.get("type") == entity_type:
                    results.append({
                        "name": ent["name"],
                        "type": ent.get("type", ""),
                        "confidence": ent.get("confidence", 0),
                        "mention_count": ent.get("mention_count", 0),
                    })
                    seen.add(ent["name"])

            # 2. 再查子串包含
            name_lower = name.lower()
            for ent in self._mem_entities.values():
                if ent["name"] in seen:
                    continue
                ent_name_lower = ent.get("name", "").lower()
                if name_lower in ent_name_lower or ent_name_lower in name_lower:
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
        """获取实体的邻域子图（支持别名解析）"""
        # 解析规范名
        canonical_name = self._resolve_entity(entity_name) or entity_name

        cypher = """
        MATCH (e:Entity {name: $name})-[r:RELATED_TO*1..$depth]-(related:Entity)
        RETURN e, r, related
        LIMIT 100
        """
        results = self._execute(cypher, {"name": canonical_name, "depth": depth})

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
            if canonical_name in self._mem_entities:
                ent = self._mem_entities[canonical_name]
                nodes.add((canonical_name, ent.get("type", "")))
            for rel in self._mem_relations:
                if rel["head"] == canonical_name or rel["tail"] == canonical_name:
                    other = rel["tail"] if rel["head"] == canonical_name else rel["head"]
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
        """获取实体关联的事件时间线（支持别名解析）"""
        canonical_name = self._resolve_entity(entity_name) or entity_name

        cypher = """
        MATCH (e:Entity {name: $name})-[r:RELATED_TO]-(related:Entity)
        WHERE related.type = 'EVENT' OR related.type = 'TIME'
        RETURN related.name as event, related.type as type,
               r.type as relation_type, r.confidence as confidence
        ORDER BY related.name
        LIMIT 50
        """
        return self._execute(cypher, {"name": canonical_name})

    # ==================================================================
    # 演示数据
    # ==================================================================

    def load_demo_data(self):
        """加载演示数据到图谱（预注册别名，自动去重）"""
        if self._mem_entities:
            return  # 已有数据，跳过

        # ========== 别名映射（优先注册，后续 upsert 自动去重） ==========
        self.register_aliases({
            # 公司别名
            "阿里": "阿里巴巴",
            "阿里集团": "阿里巴巴",
            "阿里巴巴集团": "阿里巴巴",
            "字节": "字节跳动",
            "ByteDance": "字节跳动",
            "bytedance": "字节跳动",
            "百度公司": "百度",
            "Baidu": "百度",
            "腾讯公司": "腾讯",
            "Tencent": "腾讯",
            "华为公司": "华为",
            "Huawei": "华为",
            "小米公司": "小米",
            "Xiaomi": "小米",
            "京东公司": "京东",
            "JD": "京东",
            "网易公司": "网易",
            "NetEase": "网易",
            # 大小写映射
            "openai": "OpenAI",
            "OPENAI": "OpenAI",
            "google": "Google",
            "GOOGLE": "Google",
            "meta": "Meta",
            "META": "Meta",
            "apple": "Apple",
            "APPLE": "Apple",
            "nvidia": "NVIDIA",
            "Nvidia": "NVIDIA",
            "anthropic": "Anthropic",
            "ANTHROPIC": "Anthropic",
            # 产品别名
            "gpt5": "GPT-5",
            "Gpt5": "GPT-5",
            "gpt4": "GPT-4",
            "Gpt4": "GPT-4",
            "gpt-4": "GPT-4",
            "gpt-5": "GPT-5",
            "chatgpt": "ChatGPT",
            "Chatgpt": "ChatGPT",
            "claude": "Claude",
            "gemini": "Gemini",
            "llama3": "Llama 3",
            "Llama3": "Llama 3",
            "llama-3": "Llama 3",
            "豆包": "豆包2.0",
            "豆包2": "豆包2.0",
            "混元": "混元大模型",
            "文心": "文心一言",
            "通义": "通义千问",
            "星火": "星火大模型",
            "deepmind": "Google DeepMind",
            "Deepmind": "Google DeepMind",
            "DeepMind": "Google DeepMind",
            # 人物别名
            "altman": "Sam Altman",
            "sam altman": "Sam Altman",
            "hassabis": "Demis Hassabis",
            "demis hassabis": "Demis Hassabis",
            "musk": "Elon Musk",
            "elon musk": "Elon Musk",
            "黄仁勳": "黄仁勋",
            # 地点别名
            "shanghai": "上海",
            "beijing": "北京",
            "shenzhen": "深圳",
            "hangzhou": "杭州",
            "silicon valley": "硅谷",
            "san francisco": "旧金山",
            "sf": "旧金山",
        })

        # ========== 实体数据 ==========
        entities = [
            # --- 海外科技公司 ---
            {"name": "OpenAI", "type": "ORG", "confidence": 0.95},
            {"name": "Google DeepMind", "type": "ORG", "confidence": 0.9},
            {"name": "Google", "type": "ORG", "confidence": 0.9},
            {"name": "微软", "type": "ORG", "confidence": 0.9},
            {"name": "Meta", "type": "ORG", "confidence": 0.85},
            {"name": "Apple", "type": "ORG", "confidence": 0.85},
            {"name": "NVIDIA", "type": "ORG", "confidence": 0.9},
            {"name": "Anthropic", "type": "ORG", "confidence": 0.9},
            {"name": "Mistral AI", "type": "ORG", "confidence": 0.85},

            # --- 国内科技公司 ---
            {"name": "阿里巴巴", "type": "ORG", "confidence": 0.95},
            {"name": "腾讯", "type": "ORG", "confidence": 0.9},
            {"name": "百度", "type": "ORG", "confidence": 0.9},
            {"name": "字节跳动", "type": "ORG", "confidence": 0.95},
            {"name": "华为", "type": "ORG", "confidence": 0.9},
            {"name": "小米", "type": "ORG", "confidence": 0.85},
            {"name": "商汤科技", "type": "ORG", "confidence": 0.85},
            {"name": "昆仑万维", "type": "ORG", "confidence": 0.85},
            {"name": "科大讯飞", "type": "ORG", "confidence": 0.85},
            {"name": "京东", "type": "ORG", "confidence": 0.85},
            {"name": "网易", "type": "ORG", "confidence": 0.8},

            # --- 人物 ---
            {"name": "Sam Altman", "type": "PER", "confidence": 0.95},
            {"name": "Demis Hassabis", "type": "PER", "confidence": 0.9},
            {"name": "吴泳铭", "type": "PER", "confidence": 0.9},
            {"name": "梁汝波", "type": "PER", "confidence": 0.9},
            {"name": "马化腾", "type": "PER", "confidence": 0.9},
            {"name": "李彦宏", "type": "PER", "confidence": 0.9},
            {"name": "马云", "type": "PER", "confidence": 0.9},
            {"name": "黄仁勋", "type": "PER", "confidence": 0.9},
            {"name": "Elon Musk", "type": "PER", "confidence": 0.85},
            {"name": "Dario Amodei", "type": "PER", "confidence": 0.85},

            # --- AI 模型/产品 ---
            {"name": "GPT-5", "type": "TOPIC", "confidence": 0.95},
            {"name": "GPT-4", "type": "TOPIC", "confidence": 0.9},
            {"name": "Claude", "type": "TOPIC", "confidence": 0.9},
            {"name": "Gemini", "type": "TOPIC", "confidence": 0.85},
            {"name": "Llama 3", "type": "TOPIC", "confidence": 0.85},
            {"name": "豆包2.0", "type": "TOPIC", "confidence": 0.95},
            {"name": "混元大模型", "type": "TOPIC", "confidence": 0.9},
            {"name": "文心一言", "type": "TOPIC", "confidence": 0.9},
            {"name": "通义千问", "type": "TOPIC", "confidence": 0.9},
            {"name": "星火大模型", "type": "TOPIC", "confidence": 0.85},
            {"name": "Azure", "type": "TOPIC", "confidence": 0.85},
            {"name": "TensorFlow", "type": "TOPIC", "confidence": 0.8},
            {"name": "PyTorch", "type": "TOPIC", "confidence": 0.8},
            {"name": "ChatGPT", "type": "TOPIC", "confidence": 0.9},

            # --- 地点 ---
            {"name": "上海", "type": "LOC", "confidence": 0.9},
            {"name": "北京", "type": "LOC", "confidence": 0.9},
            {"name": "深圳", "type": "LOC", "confidence": 0.85},
            {"name": "杭州", "type": "LOC", "confidence": 0.85},
            {"name": "硅谷", "type": "LOC", "confidence": 0.85},
            {"name": "旧金山", "type": "LOC", "confidence": 0.8},

            # --- 事件/领域 ---
            {"name": "多模态AI", "type": "TOPIC", "confidence": 0.85},
            {"name": "自动驾驶", "type": "TOPIC", "confidence": 0.85},
            {"name": "量子计算", "type": "TOPIC", "confidence": 0.8},
            {"name": "AI芯片", "type": "TOPIC", "confidence": 0.85},
            {"name": "大语言模型", "type": "TOPIC", "confidence": 0.9},
            {"name": "AGI", "type": "TOPIC", "confidence": 0.85},
        ]
        for e in entities:
            self.upsert_entity(e["name"], e["type"], e["confidence"])

        # ========== 关系数据 ==========
        relations = [
            # --- OpenAI 生态 ---
            {"head": "Sam Altman", "tail": "OpenAI", "relation_type": "works_for", "confidence": 0.95, "evidence": "OpenAI CEO"},
            {"head": "OpenAI", "tail": "GPT-5", "relation_type": "release", "confidence": 0.95, "evidence": "2026年5月发布GPT-5"},
            {"head": "OpenAI", "tail": "GPT-4", "relation_type": "release", "confidence": 0.9, "evidence": "GPT-4"},
            {"head": "OpenAI", "tail": "ChatGPT", "relation_type": "release", "confidence": 0.9, "evidence": "ChatGPT产品"},
            {"head": "微软", "tail": "OpenAI", "relation_type": "invests_in", "confidence": 0.9, "evidence": "最大投资方"},
            {"head": "微软", "tail": "Azure", "relation_type": "related_to", "confidence": 0.9, "evidence": "Azure云平台"},
            {"head": "GPT-5", "tail": "多模态AI", "relation_type": "related_to", "confidence": 0.9, "evidence": "多模态能力大幅提升"},

            # --- Google 生态 ---
            {"head": "Demis Hassabis", "tail": "Google DeepMind", "relation_type": "works_for", "confidence": 0.9, "evidence": "首席执行官"},
            {"head": "Google DeepMind", "tail": "Gemini", "relation_type": "release", "confidence": 0.85, "evidence": "开发Gemini模型"},
            {"head": "Google", "tail": "Google DeepMind", "relation_type": "related_to", "confidence": 0.9, "evidence": "子公司"},
            {"head": "Google", "tail": "TensorFlow", "relation_type": "release", "confidence": 0.85, "evidence": "Google开源框架"},

            # --- Anthropic ---
            {"head": "Dario Amodei", "tail": "Anthropic", "relation_type": "works_for", "confidence": 0.85, "evidence": "Anthropic CEO"},
            {"head": "Anthropic", "tail": "Claude", "relation_type": "release", "confidence": 0.9, "evidence": "Claude系列模型"},

            # --- Meta ---
            {"head": "Meta", "tail": "Llama 3", "relation_type": "release", "confidence": 0.85, "evidence": "Llama 3开源模型"},
            {"head": "Meta", "tail": "PyTorch", "relation_type": "release", "confidence": 0.85, "evidence": "PyTorch框架"},

            # --- NVIDIA ---
            {"head": "黄仁勋", "tail": "NVIDIA", "relation_type": "works_for", "confidence": 0.9, "evidence": "NVIDIA CEO"},
            {"head": "NVIDIA", "tail": "AI芯片", "relation_type": "related_to", "confidence": 0.9, "evidence": "GPU芯片龙头"},

            # --- 阿里巴巴 ---
            {"head": "吴泳铭", "tail": "阿里巴巴", "relation_type": "works_for", "confidence": 0.9, "evidence": "阿里巴巴CEO"},
            {"head": "阿里巴巴", "tail": "上海", "relation_type": "located_in", "confidence": 0.9, "evidence": "上海AI研究院"},
            {"head": "阿里巴巴", "tail": "杭州", "relation_type": "located_in", "confidence": 0.9, "evidence": "阿里巴巴总部"},
            {"head": "阿里巴巴", "tail": "通义千问", "relation_type": "release", "confidence": 0.9, "evidence": "通义千问大模型"},

            # --- 腾讯 ---
            {"head": "马化腾", "tail": "腾讯", "relation_type": "works_for", "confidence": 0.9, "evidence": "腾讯CEO"},
            {"head": "腾讯", "tail": "混元大模型", "relation_type": "release", "confidence": 0.9, "evidence": "混元大模型"},
            {"head": "腾讯", "tail": "深圳", "relation_type": "located_in", "confidence": 0.9, "evidence": "腾讯总部"},

            # --- 百度 ---
            {"head": "李彦宏", "tail": "百度", "relation_type": "works_for", "confidence": 0.9, "evidence": "百度CEO"},
            {"head": "百度", "tail": "文心一言", "relation_type": "release", "confidence": 0.9, "evidence": "文心一言4.0"},
            {"head": "百度", "tail": "北京", "relation_type": "located_in", "confidence": 0.9, "evidence": "百度总部"},
            {"head": "百度", "tail": "自动驾驶", "relation_type": "related_to", "confidence": 0.85, "evidence": "Apollo自动驾驶"},

            # --- 字节跳动 ---
            {"head": "梁汝波", "tail": "字节跳动", "relation_type": "works_for", "confidence": 0.9, "evidence": "字节跳动CEO"},
            {"head": "字节跳动", "tail": "豆包2.0", "relation_type": "release", "confidence": 0.95, "evidence": "2026年5月推出豆包2.0"},
            {"head": "字节跳动", "tail": "北京", "relation_type": "located_in", "confidence": 0.9, "evidence": "字节跳动总部"},

            # --- 华为 ---
            {"head": "华为", "tail": "星火大模型", "relation_type": "related_to", "confidence": 0.7, "evidence": "AI大模型生态"},
            {"head": "华为", "tail": "深圳", "relation_type": "located_in", "confidence": 0.9, "evidence": "华为总部"},
            {"head": "华为", "tail": "AI芯片", "relation_type": "related_to", "confidence": 0.85, "evidence": "昇腾AI芯片"},

            # --- 科大讯飞 ---
            {"head": "科大讯飞", "tail": "星火大模型", "relation_type": "release", "confidence": 0.85, "evidence": "星火认知大模型"},

            # --- 竞争关系 ---
            {"head": "商汤科技", "tail": "豆包2.0", "relation_type": "related_to", "confidence": 0.7, "evidence": "AI助手竞争"},
            {"head": "昆仑万维", "tail": "豆包2.0", "relation_type": "related_to", "confidence": 0.7, "evidence": "AI助手竞争"},
            {"head": "大语言模型", "tail": "AGI", "relation_type": "related_to", "confidence": 0.8, "evidence": "LLM是通向AGI的路径"},
        ]
        for r in relations:
            self.upsert_relation(
                head_name=r["head"],
                tail_name=r["tail"],
                relation_type=r["relation_type"],
                confidence=r["confidence"],
                evidence=r["evidence"],
            )

        logger.info(f"演示数据已加载: {len(self._mem_entities)} 实体, {len(self._mem_relations)} 关系, {len(self._aliases)} 别名")

    def close(self):
        """关闭连接"""
        if self._driver:
            self._driver.close()
            self._driver = None
            self._connected = False
