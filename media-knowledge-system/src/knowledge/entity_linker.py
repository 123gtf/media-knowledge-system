"""
实体链接与消歧模块

三级漏斗消歧策略：
1. 向量相似初筛（粗）：Embedding余弦相似度 → Top-N候选
2. 图谱结构匹配（细）：候选实体在图谱中的邻域结构相似度
3. LLM仲裁（终）：对于仍有歧义的候选，调用LLM最终判定

消歧结果：
- 匹配成功 → 返回已有实体ID
- 确认为新实体 → 返回None，由KnowledgeModeler创建新节点
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EntityLinker:
    """实体链接消歧器 —— 三级漏斗"""

    def __init__(
        self,
        graph_store: Any = None,
        mysql_repo: Any = None,
        llm_client: Any = None,
        embedding_model: Any = None,
        similarity_threshold: float = 0.85,
    ):
        self.graph_store = graph_store
        self.mysql_repo = mysql_repo
        self.llm_client = llm_client
        self.embedding_model = embedding_model
        self.similarity_threshold = similarity_threshold

    def link(
        self,
        entity_name: str,
        entity_type: str,
        aliases: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        实体链接主入口

        Returns:
            {
                "is_new": bool,
                "matched_entity": dict | None,
                "confidence": float,
                "method": str,  # "exact_match" / "vector" / "graph" / "llm"
                "candidates": list,
            }
        """
        all_aliases = [entity_name] + (aliases or [])

        # Level 0: 精确名称匹配
        exact = self._exact_match(entity_name, entity_type)
        if exact:
            return {
                "is_new": False,
                "matched_entity": exact,
                "confidence": 0.99,
                "method": "exact_match",
                "candidates": [exact],
            }

        # Level 1: 向量相似初筛
        candidates = self._vector_candidates(entity_name, entity_type, all_aliases)
        if not candidates:
            return {
                "is_new": True,
                "matched_entity": None,
                "confidence": 0.9,
                "method": "no_candidates",
                "candidates": [],
            }

        # 如果最好的候选超过阈值，直接返回
        best = candidates[0]
        if best.get("similarity", 0) >= self.similarity_threshold:
            return {
                "is_new": False,
                "matched_entity": best,
                "confidence": best["similarity"],
                "method": "vector",
                "candidates": candidates[:3],
            }

        # Level 2: 图谱结构匹配
        if self.graph_store and len(candidates) > 1:
            graph_result = self._graph_structure_match(entity_name, candidates[:3])
            if graph_result:
                return graph_result

        # Level 3: LLM仲裁
        if self.llm_client and len(candidates) > 1:
            llm_result = self._llm_arbitrate(entity_name, entity_type, candidates[:3])
            if llm_result:
                return llm_result

        # 无法确定匹配 → 作为新实体
        return {
            "is_new": True,
            "matched_entity": None,
            "confidence": 0.6,
            "method": "ambiguous",
            "candidates": candidates[:3],
        }

    def _exact_match(self, name: str, entity_type: str) -> Optional[Dict]:
        """精确匹配（Level 0）"""
        # 从MySQL查询
        if self.mysql_repo:
            result = self.mysql_repo.find_entity_by_name_type(name, entity_type)
            if result:
                return {"name": result.get("name"), "type": result.get("entity_type"),
                        "id": result.get("id"), "similarity": 1.0}

        # 从图谱查询
        if self.graph_store:
            results = self.graph_store.find_similar_entities(name, entity_type, limit=1)
            if results and results[0].get("name", "").lower() == name.lower():
                r = results[0]
                r["similarity"] = 1.0
                return r

        return None

    def _vector_candidates(
        self,
        name: str,
        entity_type: str,
        aliases: List[str],
    ) -> List[Dict]:
        """向量相似检索候选（Level 1）"""
        candidates = []

        if self.mysql_repo:
            db_candidates = self.mysql_repo.find_similar_entities(name, entity_type, limit=10)
            for c in db_candidates:
                c["similarity"] = self._name_similarity(name, c.get("name", ""))
                candidates.append(c)

        if self.graph_store:
            graph_candidates = self.graph_store.find_similar_entities(name, entity_type, limit=10)
            for c in graph_candidates:
                c["similarity"] = self._name_similarity(name, c.get("name", ""))
                # 避免重复
                if not any(existing.get("name") == c["name"] for existing in candidates):
                    candidates.append(c)

        # 按相似度排序
        candidates.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return candidates

    def _graph_structure_match(self, name: str, candidates: List[Dict]) -> Optional[Dict]:
        """图谱结构匹配（Level 2）"""
        if not self.graph_store:
            return None

        try:
            # 获取候选实体的子图，比较结构相似度
            # 简化实现：优先选择提及次数多的
            best = max(candidates, key=lambda c: c.get("mention_count", 0))
            if best.get("mention_count", 0) > 3:
                return {
                    "is_new": False,
                    "matched_entity": best,
                    "confidence": 0.8,
                    "method": "graph",
                    "candidates": candidates[:3],
                }
        except Exception as e:
            logger.warning(f"图谱结构匹配失败: {e}")

        return None

    def _llm_arbitrate(
        self,
        name: str,
        entity_type: str,
        candidates: List[Dict],
    ) -> Optional[Dict]:
        """LLM仲裁（Level 3）"""
        if not self.llm_client:
            return None

        prompt = f"""作为实体消歧仲裁员，判断新发现的实体是否与已有实体相同。

新实体：
- 名称: {name}
- 类型: {entity_type}

候选已有实体：
{json.dumps(candidates, ensure_ascii=False, indent=2)}

请判断新实体是否与某个候选实体指向同一现实对象，或确认这是一个全新实体。

只输出JSON：
{{{{
  "decision": "match|new",
  "matched_index": 0,
  "confidence": 0.85,
  "reason": "判定理由"
}}}}"""

        try:
            response = self.llm_client.call(prompt)
            result = json.loads(response) if isinstance(response, str) else response

            if result.get("decision") == "match":
                idx = result.get("matched_index", 0)
                if 0 <= idx < len(candidates):
                    matched = candidates[idx]
                    matched["similarity"] = result.get("confidence", 0.85)
                    return {
                        "is_new": False,
                        "matched_entity": matched,
                        "confidence": result.get("confidence", 0.85),
                        "method": "llm",
                        "candidates": candidates,
                        "arbitration_reason": result.get("reason", ""),
                    }

            return {
                "is_new": True,
                "matched_entity": None,
                "confidence": result.get("confidence", 0.7),
                "method": "llm",
                "candidates": candidates,
                "arbitration_reason": result.get("reason", ""),
            }
        except Exception as e:
            logger.warning(f"LLM仲裁失败: {e}")
            return None

    @staticmethod
    def _name_similarity(name1: str, name2: str) -> float:
        """名称相似度计算（简化版，可替换为embedding余弦相似度）"""
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()

        if n1 == n2:
            return 1.0
        if n1 in n2 or n2 in n1:
            return 0.85

        # Jaccard相似度（字符级2-gram）
        def bigrams(s):
            return {s[i:i+2] for i in range(len(s) - 1)}

        b1, b2 = bigrams(n1), bigrams(n2)
        if not b1 or not b2:
            return 0.0

        intersection = len(b1 & b2)
        union = len(b1 | b2)
        return intersection / union if union > 0 else 0.0
