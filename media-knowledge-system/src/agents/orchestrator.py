"""
调度协调 Agent (Orchestrator)

职责：
- 接收外部任务指令，解析意图，启动协同流程
- 汇总各Agent结果，触发报告生成
- 全局流程编排与异常兜底

不做什么：
- 不直接操作数据
- 不直接调用LLM做内容分析（交由专职Agent）
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .base import BaseAgent, Tool, tool
from .state import SharedState, TaskStatus

logger = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    """调度协调Agent —— 全局流程的总指挥"""

    def __init__(self, llm_client: Any, prompt_manager: Any = None):
        super().__init__(
            name="Orchestrator",
            role="调度协调Agent",
            goal="接收任务指令，解析意图，编排全局流程，汇总结果并触发报告生成",
            llm_client=llm_client,
        )
        self.prompt_manager = prompt_manager

        self.register_tool(self._create_intent_parser_tool())
        self.register_tool(self._create_workflow_launcher_tool())
        self.register_tool(self._create_result_aggregator_tool())

    def _create_intent_parser_tool(self) -> Tool:
        """意图解析工具"""
        @tool(
            name="parse_intent",
            description="解析用户自然语言指令，提取任务类型、数据源、时间范围和输出要求",
            schema={
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "description": "用户原始意图文本"},
                },
                "required": ["intent"],
            },
            cost="normal",
        )
        def parse_intent(intent: str):
            prompt = f"""
解析以下用户意图，提取关键要素：

意图：{intent}

请输出JSON：
{{
  "task_type": "hotspot_analysis|entity_analysis|topic_trend|data_quality",
  "data_sources": ["源1", "源2"],
  "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}},
  "target_entities": ["实体1"],
  "target_topics": ["主题1"],
  "output_format": ["markdown", "json"],
  "priority": "high|normal|low",
  "constraints": ["额外约束"]
}}
"""
            response = self.llm_client.call(prompt)
            return response

        return parse_intent.tool

    def _create_workflow_launcher_tool(self) -> Tool:
        """流程启动工具"""
        @tool(
            name="launch_workflow",
            description="基于解析后的意图启动多Agent协同工作流",
            schema={
                "type": "object",
                "properties": {
                    "parsed_intent": {"type": "object"},
                },
                "required": ["parsed_intent"],
            },
            cost="free",
        )
        def launch_workflow(parsed_intent: Dict):
            workflow_stages = [
                "planning",
                "collection",
                "analysis",
                "knowledge_modeling",
                "review",
                "report_generation",
            ]
            return {
                "workflow_id": "wf_001",
                "stages": workflow_stages,
                "estimated_duration": "~120s",
                "status": "launched",
            }

        return launch_workflow.tool

    def _create_result_aggregator_tool(self) -> Tool:
        """结果汇总工具"""
        @tool(
            name="aggregate_results",
            description="汇总各Agent的执行结果，生成统一摘要",
            schema={
                "type": "object",
                "properties": {
                    "state_summary": {"type": "object"},
                },
                "required": ["state_summary"],
            },
            cost="free",
        )
        def aggregate_results(state_summary: Dict):
            return {
                "total_articles": state_summary.get("article_count", 0),
                "total_entities": state_summary.get("entity_count", 0),
                "total_relations": state_summary.get("relation_count", 0),
                "avg_confidence": state_summary.get("avg_confidence", 0.0),
                "quality_passed": state_summary.get("quality_passed", False),
            }

        return aggregate_results.tool

    async def run(self, state: SharedState) -> SharedState:
        """执行调度协调逻辑"""
        state.current_stage = "orchestration"
        state.status = TaskStatus.RUNNING

        # Step 1: 解析意图
        intent_result = self.tools["parse_intent"].func(intent=state.intent)
        parsed = intent_result.get("data", {})
        self._log_action(state, "parse_intent", {"parsed": parsed})

        # Step 2: 启动工作流
        launch_result = self.tools["launch_workflow"].func(parsed_intent=parsed)
        self._log_action(state, "launch_workflow", launch_result.get("data", {}))

        # Step 3: 更新状态，传递解析后的意图供下游Agent使用
        state.current_stage = "orchestration_complete"

        logger.info(
            f"[Orchestrator] 意图解析完成 → 任务类型: {parsed.get('task_type', 'unknown')}, "
            f"数据源: {parsed.get('data_sources', [])}"
        )

        return state
