"""
质量审核 Agent (Reviewer)

职责：
- 对全链路产出进行质量校验
- Schema完整性检查、实体一致性校验、关系合理性审核
- 置信度过滤与修正建议
- 裁决通过/不通过，触发修正回路或放行

注册工具：
- validate_schema: Schema完整性校验
- detect_conflicts: 冲突检测
- llm_arbitrate: LLM仲裁（冲突最终裁定）
- filter_low_confidence: 低质量结果标记
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from .base import BaseAgent, Tool, tool
from .state import ReviewFlag, SharedState, TaskStatus

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    """质量审核Agent —— 全链路质检与修正"""

    def __init__(self, llm_client: Any, mysql_repo: Any = None):
        super().__init__(
            name="Reviewer",
            role="质量审核Agent",
            goal="对全链路产出进行质量校验，Schema完整性、冲突检测、LLM仲裁、置信度过滤",
            llm_client=llm_client,
        )
        self.mysql_repo = mysql_repo

        self.register_tool(self._create_schema_validator_tool())
        self.register_tool(self._create_conflict_detector_tool())
        self.register_tool(self._create_llm_arbitrator_tool())
        self.register_tool(self._create_confidence_filter_tool())

    def _create_schema_validator_tool(self) -> Tool:
        """Schema校验工具"""
        @tool(
            name="validate_schema",
            description="逐字段检查数据完整性与类型合规性",
            schema={
                "type": "object",
                "properties": {
                    "entities": {"type": "array"},
                    "relations": {"type": "array"},
                },
                "required": ["entities"],
            },
            cost="free",
        )
        def validate_schema(entities: List[Dict], relations: List[Dict] = None):
            issues = []

            # 实体Schema校验
            required_entity_fields = ["name", "type", "confidence"]
            valid_types = {"PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"}

            for i, e in enumerate(entities):
                for field in required_entity_fields:
                    if not e.get(field):
                        issues.append({
                            "type": "schema_violation",
                            "severity": "critical",
                            "target": f"entity[{i}].{field}",
                            "description": f"实体必填字段 {field} 缺失",
                            "suggestion": f"请补全 {field} 字段",
                        })
                if e.get("type") and e["type"] not in valid_types:
                    issues.append({
                        "type": "schema_violation",
                        "severity": "warning",
                        "target": f"entity[{i}].type",
                        "description": f"实体类型 {e['type']} 不在允许列表中",
                        "suggestion": f"请使用以下类型之一: {valid_types}",
                    })
                if e.get("confidence") is not None and not (0 <= e["confidence"] <= 1):
                    issues.append({
                        "type": "schema_violation",
                        "severity": "warning",
                        "target": f"entity[{i}].confidence",
                        "description": f"置信度值 {e['confidence']} 超出[0,1]范围",
                        "suggestion": "请将置信度调整到[0,1]范围内",
                    })

            # 关系Schema校验
            if relations:
                required_rel_fields = ["head", "tail", "relation_type", "confidence"]
                for j, r in enumerate(relations):
                    for field in required_rel_fields:
                        if not r.get(field):
                            issues.append({
                                "type": "schema_violation",
                                "severity": "critical",
                                "target": f"relation[{j}].{field}",
                                "description": f"关系必填字段 {field} 缺失",
                                "suggestion": f"请补全 {field} 字段",
                            })

            return {
                "valid": len([i for i in issues if i["severity"] == "critical"]) == 0,
                "issues": issues,
                "total_checked": len(entities) + len(relations or []),
            }

        return validate_schema.tool

    def _create_conflict_detector_tool(self) -> Tool:
        """冲突检测工具"""
        @tool(
            name="detect_conflicts",
            description="检测实体/关系矛盾：同名异类、重复标注、关系冲突",
            schema={
                "type": "object",
                "properties": {
                    "entities": {"type": "array"},
                    "relations": {"type": "array"},
                },
                "required": ["entities"],
            },
            cost="free",
        )
        def detect_conflicts(entities: List[Dict], relations: List[Dict] = None):
            conflicts_list = []

            # 检测同名异类实体
            name_map: Dict[str, List[Dict]] = {}
            for e in entities:
                name_map.setdefault(e["name"].lower(), []).append(e)

            for name, entries in name_map.items():
                types_set = {e["type"] for e in entries}
                if len(types_set) > 1:
                    conflicts_list.append({
                        "type": "entity_conflict",
                        "severity": "warning",
                        "target": name,
                        "description": f"实体 '{name}' 被标注为多种类型: {types_set}",
                        "suggestion": "请确认正确类型或拆分为不同实体",
                    })

            # 检测自环关系
            if relations:
                for r in relations:
                    if r.get("head") == r.get("tail"):
                        conflicts_list.append({
                            "type": "relation_error",
                            "severity": "warning",
                            "target": f"{r['head']} → {r['tail']}",
                            "description": "检测到自环关系",
                            "suggestion": "请确认该关系是否有意义",
                        })

            return {"conflicts_found": len(conflicts_list), "conflicts": conflicts_list}

        return detect_conflicts.tool

    def _create_llm_arbitrator_tool(self) -> Tool:
        """LLM仲裁工具"""
        @tool(
            name="llm_arbitrate",
            description="当自动规则无法解决的冲突时，调用LLM进行最终裁决",
            schema={
                "type": "object",
                "properties": {
                    "conflict": {"type": "object", "description": "冲突详情"},
                    "context": {"type": "string", "description": "相关上下文"},
                },
                "required": ["conflict"],
            },
            cost="normal",
        )
        def llm_arbitrate(conflict: Dict, context: str = ""):
            if not self.llm_client:
                return {"decision": "undecided", "confidence": 0.5, "reason": "LLM不可用"}

            prompt = f"""作为数据质量仲裁员，请对以下冲突做出裁决。

冲突：{json.dumps(conflict, ensure_ascii=False)}
上下文：{context[:1000]}

请做出二选一裁决，输出JSON：
{{"decision": "选中的选项", "confidence": 0.8, "reason": "裁决理由"}}

注意：必须二选一，不可创造新选项。"""

            try:
                response = self.llm_client.call(prompt)
                result = json.loads(response) if isinstance(response, str) else response
                return result
            except Exception as e:
                return {"decision": "undecided", "confidence": 0.5, "reason": str(e)}

        return llm_arbitrate.tool

    def _create_confidence_filter_tool(self) -> Tool:
        """置信度过滤工具"""
        @tool(
            name="filter_low_confidence",
            description="标记置信度低于阈值的低质量结果",
            schema={
                "type": "object",
                "properties": {
                    "entities": {"type": "array"},
                    "relations": {"type": "array"},
                    "threshold": {"type": "number", "default": 0.7},
                },
                "required": ["entities"],
            },
            cost="free",
        )
        def filter_low_confidence(entities: List[Dict], relations: List[Dict] = None, threshold: float = 0.7):
            low_quality = []

            for e in entities:
                if e.get("confidence", 0) < threshold:
                    low_quality.append({
                        "type": "low_confidence",
                        "severity": "warning",
                        "target": f"entity: {e.get('name')}",
                        "description": f"置信度 {e.get('confidence')} 低于阈值 {threshold}",
                        "suggestion": "建议重新抽取或升级为LLM调用",
                    })

            if relations:
                for r in relations:
                    if r.get("confidence", 0) < threshold:
                        low_quality.append({
                            "type": "low_confidence",
                            "severity": "warning",
                            "target": f"relation: {r.get('head')}→{r.get('tail')}",
                            "description": f"置信度 {r.get('confidence')} 低于阈值 {threshold}",
                            "suggestion": "建议重新抽取或标记为不确定",
                        })

            return {"low_quality_count": len(low_quality), "items": low_quality}

        return filter_low_confidence.tool

    async def run(self, state: SharedState) -> SharedState:
        """执行质检流程"""
        state.current_stage = "review"

        entities_dicts = [e.model_dump() for e in state.extracted_entities]
        relations_dicts = [r.model_dump() for r in state.extracted_relations]

        # Step 1: Schema校验
        schema_result = self.tools["validate_schema"].func(
            entities=entities_dicts,
            relations=relations_dicts,
        )
        schema_data = schema_result.get("data", {})
        for issue in schema_data.get("issues", []):
            state.review_flags.append(ReviewFlag(**issue))
        self._log_action(state, "schema_validation", {"valid": schema_data.get("valid")})

        # Step 2: 冲突检测
        conflict_result = self.tools["detect_conflicts"].func(
            entities=entities_dicts,
            relations=relations_dicts,
        )
        conflict_data = conflict_result.get("data", {})
        for conflict in conflict_data.get("conflicts", []):
            state.review_flags.append(ReviewFlag(**conflict))
        self._log_action(state, "conflict_detection", {"conflicts": conflict_data.get("conflicts_found")})

        # Step 3: 置信度过滤
        filter_result = self.tools["filter_low_confidence"].func(
            entities=entities_dicts,
            relations=relations_dicts,
            threshold=0.7,
        )
        filter_data = filter_result.get("data", {})
        for item in filter_data.get("items", []):
            state.review_flags.append(ReviewFlag(**item))
        self._log_action(state, "confidence_filter", {"low_quality": filter_data.get("low_quality_count")})

        # Step 4: 对严重冲突执行LLM仲裁
        critical_conflicts = [f for f in state.review_flags if f.severity == "critical"]
        if critical_conflicts and self.llm_client:
            for conflict in critical_conflicts[:3]:  # 最多仲裁3个
                arb_result = self.tools["llm_arbitrate"].func(
                    conflict=conflict.model_dump(),
                    context=f"任务: {state.intent}",
                )
                arb_data = arb_result.get("data", {})
                logger.info(f"LLM仲裁: {conflict.target} → {arb_data.get('decision')}")

        # Step 5: 综合评估
        critical_count = len([f for f in state.review_flags if f.severity == "critical"])
        warning_count = len([f for f in state.review_flags if f.severity == "warning"])
        total_flags = len(state.review_flags)

        # 计算审核置信度
        if total_flags == 0:
            review_confidence = 0.95
        elif critical_count == 0:
            review_confidence = 0.85
        else:
            review_confidence = max(0.4, 0.85 - critical_count * 0.15)

        state.confidence_scores["review"] = review_confidence

        # 判断是否通过
        passed = critical_count == 0 and review_confidence >= 0.7

        self._log_action(state, "review_verdict", {
            "passed": passed,
            "confidence": review_confidence,
            "critical_issues": critical_count,
            "warnings": warning_count,
        })

        logger.info(
            f"[Reviewer] 质检完成 → {'通过' if passed else '不通过'} "
            f"(置信度: {review_confidence:.2f}, 严重: {critical_count}, 警告: {warning_count})"
        )

        return state
