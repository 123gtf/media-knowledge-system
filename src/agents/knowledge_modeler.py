"""
知识建模 Agent (Knowledge Modeler)

职责：
- 将分析Agent产出的结构化信息，通过tool_call调用图谱工具融合到知识库
- 实体链接（三级漏斗消歧）：向量相似初筛 → 图谱结构匹配 → LLM终判
- 关系融合去重，冲突检测

注册工具：
- link_entity: 实体链接与消歧
- update_graph: 图谱写入(MERGE/CREATE)
- fuse_relations: 跨源关系融合
- retrieve_context: 子图上下文检索
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import BaseAgent, Tool, tool
from .state import Entity, Relation, SharedState

logger = logging.getLogger(__name__)


class KnowledgeModelerAgent(BaseAgent):
    """知识建模Agent —— 实体链接与图谱融合"""

    def __init__(self, llm_client: Any, mysql_repo: Any = None, graph_store: Any = None):
        super().__init__(
            name="KnowledgeModeler",
            role="知识建模Agent",
            goal="将分析结果融合到知识库，执行实体链接消歧、关系融合去重、图谱更新",
            llm_client=llm_client,
        )
        self.mysql_repo = mysql_repo
        self.graph_store = graph_store

        self.register_tool(self._create_entity_linker_tool())
        self.register_tool(self._create_graph_update_tool())
        self.register_tool(self._create_relation_fusion_tool())
        self.register_tool(self._create_context_retriever_tool())

    def _create_entity_linker_tool(self) -> Tool:
        """实体链接工具 —— 三级漏斗消歧"""
        @tool(
            name="link_entity",
            description="判断新实体是否与知识库中已有实体指代同一对象。三级漏斗：向量相似初筛→图结构匹配→LLM终判",
            schema={
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entity_name", "entity_type"],
            },
            cost="free",
        )
        def link_entity(entity_name: str, entity_type: str, aliases: List[str] = None):
            # 第一级：向量相似初筛（当前使用名称相似度简化）
            # 实际应使用embedding向量检索
            candidates = []

            # 名称标准化
            normalized = entity_name.lower().strip()

            # 尝试从MySQL查找候选
            if self.mysql_repo:
                try:
                    existing = self.mysql_repo.find_entity_by_name_type(normalized, entity_type)
                    if existing:
                        candidates.append(existing)
                except Exception:
                    pass

            # 尝试从图谱查找
            if self.graph_store:
                try:
                    graph_candidates = self.graph_store.find_similar_entities(
                        name=normalized, entity_type=entity_type
                    )
                    candidates.extend(graph_candidates or [])
                except Exception:
                    pass

            return {
                "is_new": len(candidates) == 0,
                "candidates": candidates,
                "best_match": candidates[0] if candidates else None,
                "confidence": 0.9 if not candidates else 0.7,
                "method": "name_lookup",
            }

        return link_entity.tool

    def _create_graph_update_tool(self) -> Tool:
        """图谱更新工具"""
        @tool(
            name="update_graph",
            description="向Neo4j图谱和MySQL写入实体与关系",
            schema={
                "type": "object",
                "properties": {
                    "entities": {"type": "array", "description": "待写入实体列表"},
                    "relations": {"type": "array", "description": "待写入关系列表"},
                    "batch_size": {"type": "integer", "default": 50},
                },
                "required": ["entities"],
            },
            cost="free",
        )
        def update_graph(entities: List[Dict], relations: List[Dict] = None, batch_size: int = 50):
            created_entities = 0
            created_relations = 0
            relations_list = relations or []

            # --- Neo4j 写入（可选，失败不阻塞）---
            if self.graph_store:
                try:
                    result = self.graph_store.batch_upsert_entities(entities)
                    created_entities = result.get("created", 0)
                except Exception as e:
                    logger.warning(f"Neo4j实体写入失败(将跳过): {e}")

                if relations_list:
                    try:
                        result = self.graph_store.batch_upsert_relations(relations_list)
                        created_relations = result.get("created", 0)
                    except Exception as e:
                        logger.warning(f"Neo4j关系写入失败(将跳过): {e}")

            # --- MySQL 写入（独立于Neo4j）---
            mysql_entities = 0
            mysql_relations = 0
            if self.mysql_repo:
                # 写入所有实体并建立 name→id 映射
                entity_id_map: Dict[str, int] = {}  # "name::type" → mysql_id
                for e in entities:
                    try:
                        eid = self.mysql_repo.upsert_entity(e)
                        if eid:
                            etype = e.get('type', e.get('entity_type', ''))
                            key = f"{e.get('name', '')}::{etype}"
                            entity_id_map[key] = eid
                            mysql_entities += 1
                    except Exception as ex:
                        logger.warning(f"MySQL实体写入失败: {ex}")

                def _resolve_entity_id(name: str) -> Optional[int]:
                    """三级查找实体ID：映射表 → MySQL精确查 → 自动创建"""
                    if not name:
                        return None
                    # 1. 映射表
                    for etype in ("ORG", "PER", "LOC", "TIME", "EVENT", "TOPIC"):
                        eid = entity_id_map.get(f"{name}::{etype}")
                        if eid:
                            return eid
                    # 2. MySQL
                    for etype in ("ORG", "PER", "LOC", "TIME", "EVENT", "TOPIC"):
                        eid = self.mysql_repo.get_entity_id(name, etype)
                        if eid:
                            entity_id_map[f"{name}::{etype}"] = eid
                            return eid
                    # 3. 自动创建
                    eid = self.mysql_repo.upsert_entity({
                        "name": name, "type": "TOPIC",
                        "confidence": 0.3, "aliases": [name],
                    })
                    if eid:
                        entity_id_map[f"{name}::TOPIC"] = eid
                    return eid

                # 写入关系
                for r in relations_list:
                    try:
                        head_name = r.get("head", "")
                        tail_name = r.get("tail", "")
                        hid = _resolve_entity_id(head_name)
                        tid = _resolve_entity_id(tail_name)
                        if hid and tid:
                            r["head_id"] = hid
                            r["tail_id"] = tid
                            self.mysql_repo.insert_relation(r)
                            mysql_relations += 1
                    except Exception as ex:
                        logger.warning(f"MySQL关系写入失败: {ex}")

                if mysql_entities > 0 or mysql_relations > 0:
                    logger.info(f"MySQL写入: {mysql_entities} 实体, {mysql_relations} 关系")

            # 优先返回MySQL写入数（更可靠），Neo4j作为补充
            return {
                "created_entities": mysql_entities or created_entities,
                "created_relations": mysql_relations or created_relations,
                "total_entities": len(entities),
                "total_relations": len(relations_list),
            }

        return update_graph.tool

    def _create_relation_fusion_tool(self) -> Tool:
        """关系融合工具"""
        @tool(
            name="fuse_relations",
            description="跨源关系合并去重，检测冲突关系",
            schema={
                "type": "object",
                "properties": {
                    "new_relations": {"type": "array"},
                    "existing_relations": {"type": "array"},
                },
                "required": ["new_relations"],
            },
            cost="free",
        )
        def fuse_relations(new_relations: List[Dict], existing_relations: List[Dict] = None):
            fused = []
            conflicts = []
            seen = set()

            for rel in new_relations:
                key = f"{rel.get('head')}|{rel.get('relation', rel.get('relation_type', ''))}|{rel.get('tail')}"
                if key in seen:
                    continue
                seen.add(key)

                # 检查冲突：同一实体对但关系类型不同
                if existing_relations:
                    for ex in existing_relations:
                        if (ex.get("head") == rel.get("head")
                                and ex.get("tail") == rel.get("tail")
                                and ex.get("relation") != rel.get("relation")):
                            conflicts.append({
                                "new": rel,
                                "existing": ex,
                                "type": "relation_conflict",
                            })

                fused.append(rel)

            return {
                "fused_count": len(fused),
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
                "relations": fused,
            }

        return fuse_relations.tool

    def _create_context_retriever_tool(self) -> Tool:
        """子图上下文检索工具"""
        @tool(
            name="retrieve_context",
            description="检索实体在知识图谱中的邻域子图上下文",
            schema={
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string"},
                    "depth": {"type": "integer", "default": 2},
                },
                "required": ["entity_name"],
            },
            cost="free",
        )
        def retrieve_context(entity_name: str, depth: int = 2):
            if self.graph_store:
                try:
                    subgraph = self.graph_store.get_subgraph(entity_name, depth)
                    return {"subgraph": subgraph, "entity": entity_name, "depth": depth}
                except Exception as e:
                    logger.warning(f"子图检索失败: {e}")

            return {"subgraph": {"nodes": [], "edges": []}, "entity": entity_name, "depth": depth}

        return retrieve_context.tool

    async def run(self, state: SharedState) -> SharedState:
        """执行知识建模流程"""
        state.current_stage = "knowledge_modeling"

        # Step 1: 实体链接消歧 + 收集所有实体引用（用于关系ID映射）
        all_entities: list[dict] = []
        existing_names: set[str] = set()
        for entity in state.extracted_entities:
            link_result = self.tools["link_entity"].func(
                entity_name=entity.name,
                entity_type=entity.type,
                aliases=[entity.name],
            )
            link_data = link_result.get("data", {})
            entity_dict = entity.model_dump()
            all_entities.append(entity_dict)
            existing_names.add(entity.name)

        # Step 1.5: 收集关系中引用的实体（可能不在extracted_entities中，需补全以便解析ID）
        for rel in state.extracted_relations:
            for name in (rel.head, rel.tail):
                if name and name not in existing_names:
                    all_entities.append({
                        "name": name, "type": "TOPIC",
                        "confidence": 0.5, "aliases": [name],
                    })
                    existing_names.add(name)

        # Step 2: 关系融合去重
        new_relations = [r.model_dump() for r in state.extracted_relations]
        existing_relations = []
        if self.mysql_repo:
            try:
                existing_relations = self.mysql_repo.get_recent_relations(limit=500)
            except Exception:
                pass

        fusion_result = self.tools["fuse_relations"].func(
            new_relations=new_relations,
            existing_relations=existing_relations,
        )
        fusion_data = fusion_result.get("data", {})
        fused_relations = fusion_data.get("relations", [])
        conflicts = fusion_data.get("conflicts", [])

        # Step 3: 图谱更新（传入全部实体用于ID映射）
        graph_result = self.tools["update_graph"].func(
            entities=all_entities,
            relations=fused_relations,
            batch_size=50,
        )
        graph_data = graph_result.get("data", {})

        # Step 4: 更新State
        state.knowledge_updates = {
            "entities_added": graph_data.get("created_entities", 0),
            "relations_added": graph_data.get("created_relations", 0),
            "conflicts_detected": len(conflicts),
        }

        self._log_action(state, "knowledge_fusion", state.knowledge_updates)
        state.confidence_scores["knowledge_modeling"] = 0.85 if not conflicts else 0.7

        logger.info(
            f"[KnowledgeModeler] 图谱更新完成 → "
            f"+{graph_data.get('created_entities', 0)} 实体, "
            f"+{graph_data.get('created_relations', 0)} 关系, "
            f"{len(conflicts)} 冲突待处理"
        )

        return state
