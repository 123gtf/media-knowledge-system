"""
关系抽取模块

基于LLM少样本抽取实体间的语义关系，包括：
- 任职于 (works_for)
- 位于 (located_in)
- 参与 (participates_in)
- 收购 (acquires)
- 合作 (cooperates_with)
- 投资 (invests_in)
- 关联 (related_to)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RelationExtractor:
    """关系抽取器 —— 基于LLM"""

    RELATION_TYPES = [
        "works_for", "located_in", "participates_in",
        "acquires", "cooperates_with", "invests_in",
        "related_to",
    ]

    def __init__(self, llm_client: Any):
        """
        Args:
            llm_client: LLM调用客户端
        """
        self.llm_client = llm_client

    def extract(
        self,
        text: str,
        entities: List[Dict[str, Any]],
        relation_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        从文本中抽取关系三元组

        Args:
            text: 输入文本
            entities: 已识别的实体列表
            relation_types: 目标关系类型（可选）

        Returns:
            [{"head": "实体1", "relation": "works_for", "tail": "实体2", "confidence": 0.9, "evidence": "..."}]
        """
        if not entities or len(entities) < 2:
            return []

        if not self.llm_client:
            return self._extract_rule(text, entities)

        target_rels = relation_types or self.RELATION_TYPES

        prompt = self._build_prompt(text, entities, target_rels)
        try:
            response = self.llm_client.call(prompt)
            result = json.loads(response) if isinstance(response, str) else response
            relations = result.get("relations", [])

            # 校验关系类型
            valid_relations = []
            for rel in relations:
                if rel.get("relation") in target_rels:
                    valid_relations.append(rel)
                else:
                    rel["relation"] = "related_to"
                    valid_relations.append(rel)

            return valid_relations
        except Exception as e:
            logger.warning(f"关系抽取失败: {e}")
            return self._extract_rule(text, entities)

    def _build_prompt(
        self,
        text: str,
        entities: List[Dict],
        relation_types: List[str],
    ) -> str:
        """构建关系抽取Prompt"""
        entity_names = [e["text"] for e in entities]
        rel_desc = "\n".join(
            f"- {r}: {self._relation_description(r)}"
            for r in relation_types
        )

        return f"""从以下文本中抽取实体间的关系三元组。

文本：
{text[:2000]}

已识别实体：{json.dumps(entity_names, ensure_ascii=False)}

可用关系类型：
{rel_desc}

请只输出JSON，格式如下：
{{{{
  "relations": [
    {{{{
      "head": "主体实体名",
      "relation": "{relation_types[0]}",
      "tail": "宾语实体名",
      "confidence": 0.9,
      "evidence": "原文证据片段"
    }}}}
  ]
}}}}

注意：
1. head和tail必须来自已识别实体列表
2. 每条关系必须有原文证据
3. 不要输出不相关的关系
4. 置信度根据证据的明确程度设定"""

    @staticmethod
    def _relation_description(rel_type: str) -> str:
        """关系类型描述"""
        descriptions = {
            "works_for": "人物→组织，表示任职/工作关系",
            "located_in": "组织/事件→地点，表示位置关系",
            "participates_in": "人物/组织→事件，表示参与关系",
            "acquires": "组织→组织，表示收购关系",
            "cooperates_with": "组织→组织，表示合作关系",
            "invests_in": "组织→组织，表示投资关系",
            "related_to": "其他语义关联",
        }
        return descriptions.get(rel_type, "通用关系")

    def _extract_rule(self, text: str, entities: List[Dict]) -> List[Dict]:
        """规则降级抽取（基于共现）"""
        if len(entities) < 2:
            return []

        relations = []
        entity_names = [e["text"] for e in entities]

        # 简单共现：同句出现的实体视为关联
        sentences = text.replace("！", "。").replace("？", "。").replace("\n", "。").split("。")

        for sentence in sentences:
            present = [name for name in entity_names if name in sentence]
            if len(present) >= 2:
                for i in range(len(present)):
                    for j in range(i + 1, len(present)):
                        relations.append({
                            "head": present[i],
                            "relation": "related_to",
                            "tail": present[j],
                            "confidence": 0.5,
                            "evidence": sentence.strip()[:200],
                        })

        return relations
