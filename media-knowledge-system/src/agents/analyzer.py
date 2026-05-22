"""
分析抽取 Agent (Analyzer)

职责：
- 对清洗后的文本，通过tool_call调用小模型工具与LLM工具执行NLP信息抽取
- 分层调用策略：小模型粗筛 → LLM精抽

注册工具：
- extract_entities: 命名实体识别（小模型本地推理）
- extract_relations: 关系抽取（LLM）
- extract_events: 事件抽取
- summarize: 摘要生成

成本优化策略：
  文本输入
    ├── 第一步：实体识别（小模型，零API成本）
    │    仅对有实体的句子继续后续处理
    ├── 第二步：关系抽取（LLM，仅处理含实体的句子）
    ├── 第三步：事件抽取（含时间/地点实体的文本才触发）
    └── 第四步：摘要生成（LLM，全文本摘要）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from .base import BaseAgent, Tool, tool
from .state import Entity, Event, Relation, SharedState

logger = logging.getLogger(__name__)


class AnalyzerAgent(BaseAgent):
    """分析抽取Agent —— NLP信息抽取流水线"""

    def __init__(self, llm_client: Any, small_model_ner: Any = None):
        super().__init__(
            name="Analyzer",
            role="分析抽取Agent",
            goal="对清洗后的文本执行分层NLP信息抽取（NER、关系、事件、摘要），小模型粗筛+LLM精抽",
            llm_client=llm_client,
        )
        self.small_model_ner = small_model_ner

        self.register_tool(self._create_ner_tool())
        self.register_tool(self._create_relation_tool())
        self.register_tool(self._create_event_tool())
        self.register_tool(self._create_summarizer_tool())

    def _create_ner_tool(self) -> Tool:
        """实体识别工具"""
        @tool(
            name="extract_entities",
            description="使用小模型进行命名实体识别(PER/ORG/LOC/TIME/EVENT/TOPIC)，本地推理零API成本",
            schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待识别文本"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"]},
                        "description": "目标实体类型",
                    },
                },
                "required": ["text"],
            },
            cost="free",
        )
        def extract_entities(text: str, types: List[str] = None):
            entities = []
            target_types = set(types) if types else {"PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"}

            if self.small_model_ner:
                try:
                    raw_entities = self.small_model_ner.predict(text)
                    entities = [
                        {"name": e["text"], "type": e["type"], "confidence": e.get("confidence", 0.85)}
                        for e in raw_entities if e.get("type") in target_types
                    ]
                except Exception as e:
                    logger.warning(f"小模型NER失败，降级为规则匹配: {e}")
                    entities = _rule_based_ner(text, target_types)
            else:
                entities = _rule_based_ner(text, target_types)

            return {"entities": entities, "count": len(entities), "method": "small_model" if self.small_model_ner else "rule"}

        return extract_entities.tool

    def _create_relation_tool(self) -> Tool:
        """关系抽取工具"""
        @tool(
            name="extract_relations",
            description="使用LLM进行实体间语义关系抽取，输出主-谓-宾三元组",
            schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "待抽取文本"},
                    "entities": {"type": "array", "description": "已识别的实体列表"},
                },
                "required": ["text", "entities"],
            },
            cost="normal",
        )
        def extract_relations(text: str, entities: List[Dict]):
            if not entities or len(entities) < 2:
                return {"relations": [], "count": 0}

            if not self.llm_client:
                return {"relations": [], "count": 0}

            prompt = f"""从以下文本中抽取主体-关系-宾语三元组。

文本：{text[:2000]}

已识别实体：{json.dumps(entities, ensure_ascii=False)}

请输出JSON，只输出JSON：
{{"relations": [
  {{"head": "主体", "relation": "关系类型", "tail": "宾语", "confidence": 0.9, "evidence": "原文证据"}}
]}}

关系类型包括：任职于(works_for)、位于(located_in)、参与(participates_in)、收购(acquires)、合作(cooperates_with)、投资(invests_in)、关联(related_to)"""

            try:
                response = self.llm_client.call(prompt)
                result = json.loads(response) if isinstance(response, str) else response
                relations = result.get("relations", [])
                return {"relations": relations, "count": len(relations)}
            except Exception as e:
                logger.warning(f"关系抽取失败: {e}")
                return {"relations": [], "count": 0}

        return extract_relations.tool

    def _create_event_tool(self) -> Tool:
        """事件抽取工具"""
        @tool(
            name="extract_events",
            description="从文本中识别事件及其参与方、时间、地点",
            schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "entities": {"type": "array"},
                },
                "required": ["text"],
            },
            cost="normal",
        )
        def extract_events(text: str, entities: List[Dict] = None):
            # 检查是否包含时间/地点实体（才值得抽取事件）
            has_event_clue = False
            if entities:
                for e in entities:
                    if e.get("type") in ("TIME", "LOC", "EVENT"):
                        has_event_clue = True
                        break

            if not has_event_clue and len(text) < 500:
                return {"events": [], "count": 0, "skipped": True}

            if not self.llm_client:
                return {"events": [], "count": 0}

            prompt = f"""从文本中识别事件。

文本：{text[:1500]}

已识别实体：{json.dumps(entities or [], ensure_ascii=False)}

输出JSON：
{{"events": [
  {{"name": "事件名", "trigger": "触发词", "participants": [{{"entity": "X", "role": "主体/客体"}}], "location": "地点", "time": "时间", "confidence": 0.8}}
]}}"""

            try:
                response = self.llm_client.call(prompt)
                result = json.loads(response) if isinstance(response, str) else response
                return {"events": result.get("events", []), "count": len(result.get("events", []))}
            except Exception as e:
                logger.warning(f"事件抽取失败: {e}")
                return {"events": [], "count": 0}

        return extract_events.tool

    def _create_summarizer_tool(self) -> Tool:
        """摘要生成工具"""
        @tool(
            name="summarize",
            description="使用LLM生成文本摘要，保留5W1H关键要素",
            schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "max_length": {"type": "integer", "default": 200},
                },
                "required": ["text"],
            },
            cost="normal",
        )
        def summarize(text: str, max_length: int = 200):
            if not self.llm_client:
                # 降级为抽取式摘要
                sentences = text.split("。")
                summary = "。".join(sentences[:3]) if len(sentences) > 3 else text
                return {"summary": summary[:max_length], "length": len(summary[:max_length]), "method": "extractive"}

            prompt = f"""将以下文本总结为不超过{max_length}字的摘要，保留5W1H要素：

{text[:3000]}

输出JSON：
{{"summary": "摘要", "key_points": ["要点1", "要点2", "要点3"]}}"""

            try:
                response = self.llm_client.call(prompt)
                result = json.loads(response) if isinstance(response, str) else response
                return {"summary": result.get("summary", ""), "key_points": result.get("key_points", []), "method": "llm"}
            except Exception:
                return {"summary": text[:max_length], "method": "truncation"}

        return summarize.tool

    async def run(self, state: SharedState) -> SharedState:
        """执行分析流程 —— 分层调用策略"""
        state.current_stage = "analysis"

        entity_set: Dict[str, Entity] = {}  # name+type → Entity，自动去重

        for doc in state.cleaned_documents:
            text = doc.content
            if len(text) < 50:
                continue

            # 第一步：实体识别（小模型，零成本）
            ner_result = self.tools["extract_entities"].func(
                text=text,
                types=["PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"],
            )
            entities_data = ner_result.get("data", {}).get("entities", [])

            # 第二步：关系抽取（LLM，仅处理有实体的文本）
            if len(entities_data) >= 2:
                rel_result = self.tools["extract_relations"].func(
                    text=text,
                    entities=entities_data,
                )
                rels = rel_result.get("data", {}).get("relations", [])
                for rel in rels:
                    state.extracted_relations.append(Relation(
                        head=rel.get("head", ""),
                        tail=rel.get("tail", ""),
                        relation_type=rel.get("relation", "related_to"),
                        confidence=rel.get("confidence", 0.8),
                        evidence=rel.get("evidence", ""),
                        source_article_id=doc.id,
                    ))

            # 第三步：事件抽取（含时间/地点实体才触发）
            event_result = self.tools["extract_events"].func(
                text=text,
                entities=entities_data,
            )
            events_data = event_result.get("data", {}).get("events", [])
            for ev in events_data:
                state.extracted_events.append(Event(
                    name=ev.get("name", ""),
                    trigger_word=ev.get("trigger", ""),
                    participants=ev.get("participants", []),
                    location=ev.get("location"),
                    time=ev.get("time"),
                    confidence=ev.get("confidence", 0.8),
                    source=doc.source,
                ))

            # 第四步：摘要生成
            summ_result = self.tools["summarize"].func(text=text, max_length=200)

            # 存储实体（去重）
            for e in entities_data:
                key = f"{e['name']}::{e['type']}"
                if key not in entity_set:
                    entity_set[key] = Entity(
                        name=e["name"],
                        type=e["type"],
                        confidence=e.get("confidence", 0.85),
                        source=doc.source,
                    )
                else:
                    # 更新置信度（取max）
                    existing = entity_set[key]
                    existing.confidence = max(existing.confidence, e.get("confidence", 0.85))

        state.extracted_entities = list(entity_set.values())

        # 计算分析阶段置信度
        if state.extracted_entities:
            avg_conf = sum(e.confidence for e in state.extracted_entities) / len(state.extracted_entities)
            state.confidence_scores["analysis_ner"] = avg_conf
        if state.extracted_relations:
            avg_conf = sum(r.confidence for r in state.extracted_relations) / len(state.extracted_relations)
            state.confidence_scores["analysis_relation"] = avg_conf

        self._log_action(state, "extract_info", {
            "entities": len(state.extracted_entities),
            "relations": len(state.extracted_relations),
            "events": len(state.extracted_events),
        })

        logger.info(
            f"[Analyzer] 分析完成 → {len(state.extracted_entities)} 实体, "
            f"{len(state.extracted_relations)} 关系, "
            f"{len(state.extracted_events)} 事件"
        )

        return state


def _rule_based_ner(text: str, target_types: set) -> List[Dict]:
    """基于规则的NER降级方案，委托给 NERExtractor"""
    from src.nlp.ner import NERExtractor
    extractor = NERExtractor(engine="rule")
    raw = extractor.predict(text, list(target_types))
    return [
        {"name": e["text"], "type": e["type"], "confidence": e["confidence"]}
        for e in raw
    ]
