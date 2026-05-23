"""
LangGraph 编排图 —— 多Agent协同的DAG执行引擎

基于LangGraph StateGraph实现：
- 节点 = Agent执行节点
- 边 = 数据/控制流
- 条件路由 = 质检通过/不通过的分支逻辑
- 修正回路 = 失败回退到上一阶段重试

执行流程：
  START → plan → collect → analyze → model → review
                                                      ├── 通过 → report → END
                                                      └── 不通过 → correct → review(重新)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Literal

from .state import SharedState, TaskStatus

logger = logging.getLogger(__name__)


class GraphOrchestrator:
    """
    基于LangGraph的编排框架

    简化实现 —— 直接通过代码编排Agent调用顺序与条件路由。
    生产环境可替换为完整的langgraph StateGraph，当前实现保留
    相同的接口和语义，便于后续迁移。
    """

    def __init__(self, agents: Dict[str, Any]):
        self.agents = agents
        self._nodes = [
            "plan", "collect", "analyze", "model", "review", "report", "correct"
        ]
        self._sources_config: Dict[str, Any] = {}

    async def _plan_node(self, state: SharedState) -> SharedState:
        """规划节点"""
        logger.info("─" * 40)
        logger.info("[Node: plan] Planner Agent 执行中...")
        planner = self.agents.get("planner")
        if planner:
            state = await planner.run(state, sources_config=self._sources_config)
        return state

    async def _collect_node(self, state: SharedState) -> SharedState:
        """采集节点"""
        logger.info("─" * 40)
        logger.info("[Node: collect] Collector Agent 执行中...")
        collector = self.agents.get("collector")
        if collector:
            state = await collector.run(state, sources_config=self._sources_config)
        return state

    async def _analyze_node(self, state: SharedState) -> SharedState:
        """分析节点"""
        logger.info("─" * 40)
        logger.info("[Node: analyze] Analyzer Agent 执行中...")
        analyzer = self.agents.get("analyzer")
        if analyzer:
            state = await analyzer.run(state)
        return state

    async def _model_node(self, state: SharedState) -> SharedState:
        """知识建模节点"""
        logger.info("─" * 40)
        logger.info("[Node: model] KnowledgeModeler Agent 执行中...")
        modeler = self.agents.get("knowledge_modeler")
        if modeler:
            state = await modeler.run(state)
        return state

    async def _review_node(self, state: SharedState) -> SharedState:
        """质检节点"""
        logger.info("─" * 40)
        logger.info("[Node: review] Reviewer Agent 执行中...")
        reviewer = self.agents.get("reviewer")
        if reviewer:
            state = await reviewer.run(state)
        return state

    async def _correct_node(self, state: SharedState) -> SharedState:
        """修正节点 —— 回退到分析阶段重试"""
        logger.info("─" * 40)
        logger.info(f"[Node: correct] 修正回路触发 (第{state.correction_count + 1}次)...")

        state.correction_count += 1
        state.current_stage = "correction"

        # 清除低质量结果
        state.extracted_entities = [
            e for e in state.extracted_entities
            if e.confidence >= 0.7
        ]
        state.extracted_relations = [
            r for r in state.extracted_relations
            if r.confidence >= 0.7
        ]

        # 重新分析
        analyzer = self.agents.get("analyzer")
        if analyzer:
            state = await analyzer.run(state)

        # 重新建模
        modeler = self.agents.get("knowledge_modeler")
        if modeler:
            state = await modeler.run(state)

        logger.info(f"[correct] 修正完成，修正次数: {state.correction_count}")
        return state

    async def _report_node(self, state: SharedState) -> SharedState:
        """报告生成节点"""
        logger.info("─" * 40)
        logger.info("[Node: report] 报告生成中...")

        state.current_stage = "report_generation"
        state.status = TaskStatus.SUCCESS

        report = self._generate_report(state)
        state.report = report

        # 同时生成JSON格式
        state.report_json = {
            "task_id": state.task_id,
            "intent": state.intent,
            "status": state.status.value,
            "generated_at": datetime.now().isoformat(),
            "statistics": {
                "raw_articles": len(state.raw_documents),
                "cleaned_articles": len(state.cleaned_documents),
                "extracted_entities": len(state.extracted_entities),
                "extracted_relations": len(state.extracted_relations),
                "extracted_events": len(state.extracted_events),
                "review_flags": len(state.review_flags),
            },
            "confidence_scores": state.confidence_scores,
            "top_entities": [
                {"name": e.name, "type": e.type, "confidence": e.confidence}
                for e in sorted(state.extracted_entities, key=lambda x: x.confidence, reverse=True)[:20]
            ],
            "top_relations": [
                {"head": r.head, "relation": r.relation_type, "tail": r.tail, "confidence": r.confidence}
                for r in sorted(state.extracted_relations, key=lambda x: x.confidence, reverse=True)[:20]
            ],
        }

        logger.info("[report] 报告生成完成")
        return state

    def _review_decision(self, state: SharedState) -> Literal["merge", "correct"]:
        """质检决策：通过→合并，不通过→修正"""
        confidence = state.confidence_scores.get("review", 0.0)
        critical_flags = [f for f in state.review_flags if f.severity == "critical"]

        if confidence >= 0.7 and not critical_flags:
            return "merge"
        elif state.correction_count >= state.max_corrections:
            logger.warning(
                f"修正次数已达上限({state.max_corrections})，标记为PARTIAL并放行"
            )
            state.status = TaskStatus.PARTIAL
            return "merge"
        else:
            return "correct"

    def _generate_report(self, state: SharedState) -> str:
        """生成Markdown格式分析报告"""
        entities_top = sorted(
            state.extracted_entities, key=lambda e: e.confidence, reverse=True
        )[:20]
        relations_top = sorted(
            state.extracted_relations, key=lambda r: r.confidence, reverse=True
        )[:20]

        avg_confidence = 0.0
        if state.confidence_scores:
            avg_confidence = sum(state.confidence_scores.values()) / len(state.confidence_scores)

        entity_rows = "\n".join(
            f"| {e.name} | {e.type} | {e.confidence:.2%} | {e.source} |"
            for e in entities_top
        )

        relation_rows = "\n".join(
            f"| {r.head} | {r.relation_type} | {r.tail} | {r.confidence:.2%} |"
            for r in relations_top
        )

        quality_status = "通过" if state.status == TaskStatus.SUCCESS else "部分通过(有人工复核建议)"

        report = f"""# 媒体数据分析报告

**报告ID**: {state.task_id}
**生成时间**: {datetime.now().isoformat()}
**数据意图**: {state.intent}
**最终状态**: {state.status.value}

---

## 一、执行摘要

- 采集文章数: **{len(state.raw_documents)}** 篇
- 有效清洗数: **{len(state.cleaned_documents)}** 篇
- 提取实体数: **{len(state.extracted_entities)}** 个
- 提取关系数: **{len(state.extracted_relations)}** 条
- 提取事件数: **{len(state.extracted_events)}** 个
- 平均置信度: **{avg_confidence:.2%}**
- 质检状态: **{quality_status}**

## 二、各阶段置信度

| 阶段 | 置信度 |
|------|--------|
{chr(10).join(f"| {stage} | {score:.2%} |" for stage, score in state.confidence_scores.items())}

## 三、关键实体 Top-20

| 实体名称 | 类型 | 置信度 | 来源 |
|----------|------|--------|------|
{entity_rows}

## 四、关键关系 Top-20

| 主体 | 关系 | 宾语 | 置信度 |
|------|------|------|--------|
{relation_rows}

## 五、质检标记

共发现 **{len(state.review_flags)}** 条质检标记：
{chr(10).join(f"- [{f.severity}] {f.type}: {f.target} — {f.description}" for f in state.review_flags[:20])}

## 六、知识库更新摘要

- 新增实体: {state.knowledge_updates.get('entities_added', 0)} 个
- 新增关系: {state.knowledge_updates.get('relations_added', 0)} 条
- 检测冲突: {state.knowledge_updates.get('conflicts_detected', 0)} 条

## 七、执行日志

```
{chr(10).join(f"[{log.get('timestamp', '')}] {log.get('agent', '')}: {log.get('action', '')} → {log.get('status', '')}" for log in state.execution_log)}
```

---

*本报告由多智能体协同系统自动生成，数据基于指定时间窗口内的媒体来源。*
"""
        return report

    async def run(self, task_id: str, intent: str, demo_documents=None, sources_config=None) -> SharedState:
        """
        执行完整的多Agent协同流程

        流程：
        1. plan → 2. collect → 3. analyze → 4. model → 5. review
           ├── 通过 → 6. report → END
           └── 不通过 → correct → review (循环)

        Args:
            task_id: 任务ID
            intent: 用户意图
            demo_documents: 可选，内置演示数据列表（跳过网络采集）
            sources_config: 数据源配置 {"rss": [...], "web": [...], "social_api": [...]}
        """
        logger.info("=" * 60)
        logger.info(f"任务启动: {task_id}")
        logger.info(f"意图: {intent}")
        logger.info("=" * 60)

        # 保存数据源配置供各节点使用
        self._sources_config = sources_config or {}

        state = SharedState(
            task_id=task_id,
            intent=intent,
            status=TaskStatus.PENDING,
        )

        try:
            # Node 1: 规划
            state = await self._plan_node(state)

            # Node 2: 采集 (有demo数据则直接注入，否则网络采集)
            if demo_documents:
                logger.info(f"[Demo模式] 注入 {len(demo_documents)} 篇内置新闻样本")
                state.raw_documents = list(demo_documents)
                state.cleaned_documents = list(demo_documents)
                state.confidence_scores["collection"] = 0.99
            else:
                state = await self._collect_node(state)
                # 生产模式采集为空的兜底
                if not state.raw_documents:
                    logger.warning("[Production] 网络采集为空，请检查数据源配置和网络连接")

            # Node 3: 分析
            state = await self._analyze_node(state)

            # Node 4: 知识建模
            state = await self._model_node(state)

            # Node 5: 质检 → 条件路由
            state = await self._review_node(state)
            decision = self._review_decision(state)

            while decision == "correct":
                state = await self._correct_node(state)
                state = await self._review_node(state)
                decision = self._review_decision(state)

            # Node 6: 报告生成
            state = await self._report_node(state)

        except Exception as e:
            logger.error(f"流程执行异常: {e}", exc_info=True)
            state.status = TaskStatus.FAILED
            state.report = f"# 执行失败报告\n\n任务 {task_id} 执行异常: {str(e)}"

        logger.info("=" * 60)
        logger.info(f"任务完成: {task_id} → 状态: {state.status.value}")
        logger.info("=" * 60)

        return state
