"""
任务规划 Agent (Planner)

职责：
- 将Orchestrator传来的高层意图分解为可执行的原子任务DAG
- 定义任务间依赖与并行策略
- 按数据链路纵向切分 + 按数据源维度横向并行

分解逻辑：
- 纵向：采集 → 清洗 → 分析 → 建模 → 质检
- 横向：不同数据源/实体维度可并行
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List

from .base import BaseAgent, Tool, tool
from .state import SharedState, TaskDAG, TaskNode

logger = logging.getLogger(__name__)


class PlannerAgent(BaseAgent):
    """任务规划Agent —— 意图 → 任务DAG"""

    def __init__(self, llm_client: Any):
        super().__init__(
            name="Planner",
            role="任务规划Agent",
            goal="将高层意图分解为可执行的原子任务DAG，定义依赖与并行策略",
            llm_client=llm_client,
        )
        self.register_tool(self._create_dag_builder_tool())
        self.register_tool(self._create_dependency_resolver_tool())
        self.register_tool(self._create_parallelism_optimizer_tool())

    def _create_dag_builder_tool(self) -> Tool:
        """DAG构建工具"""
        @tool(
            name="build_task_dag",
            description="根据意图构建任务DAG，按数据链路纵向分解+按数据源横向并行",
            schema={
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "data_sources": {"type": "array", "items": {"type": "string"}},
                    "task_type": {"type": "string"},
                },
                "required": ["intent"],
            },
            cost="normal",
        )
        def build_task_dag(intent: str, data_sources: List[str] = None, task_type: str = "hotspot_analysis"):
            sources = data_sources or ["rss://default", "web://default"]

            nodes = []

            # Stage 1: 采集任务（按数据源横向并行）
            for i, src in enumerate(sources):
                nodes.append(TaskNode(
                    node_id=f"collect_{i}",
                    agent_type="collector",
                    task_type="fetch",
                    params={"source": src},
                    dependencies=[],
                    priority=10,
                    estimated_duration_ms=5000,
                ))

            # Stage 2: 清洗任务（按采集结果并行）
            for i in range(len(sources)):
                nodes.append(TaskNode(
                    node_id=f"clean_{i}",
                    agent_type="collector",
                    task_type="clean",
                    params={},
                    dependencies=[f"collect_{i}"],
                    priority=9,
                    estimated_duration_ms=2000,
                ))

            # Stage 3: 分析任务（NER + 关系 + 事件 可并行）
            nodes.append(TaskNode(
                node_id="analyze_ner",
                agent_type="analyzer",
                task_type="ner_extraction",
                params={"types": ["PER", "ORG", "LOC", "TIME", "EVENT", "TOPIC"]},
                dependencies=[f"clean_{i}" for i in range(len(sources))],
                priority=8,
                estimated_duration_ms=3000,
            ))
            nodes.append(TaskNode(
                node_id="analyze_relation",
                agent_type="analyzer",
                task_type="relation_extraction",
                params={},
                dependencies=["analyze_ner"],
                priority=7,
                estimated_duration_ms=5000,
            ))
            nodes.append(TaskNode(
                node_id="analyze_event",
                agent_type="analyzer",
                task_type="event_extraction",
                params={},
                dependencies=["analyze_ner"],
                priority=7,
                estimated_duration_ms=4000,
            ))
            nodes.append(TaskNode(
                node_id="analyze_summary",
                agent_type="analyzer",
                task_type="summarization",
                params={"max_length": 200},
                dependencies=[f"clean_{i}" for i in range(len(sources))],
                priority=6,
                estimated_duration_ms=3000,
            ))

            # Stage 4: 知识建模
            nodes.append(TaskNode(
                node_id="knowledge_model",
                agent_type="knowledge_modeler",
                task_type="entity_linking_and_fusion",
                params={},
                dependencies=["analyze_ner", "analyze_relation", "analyze_event"],
                priority=5,
                estimated_duration_ms=4000,
            ))

            # Stage 5: 质检
            nodes.append(TaskNode(
                node_id="quality_review",
                agent_type="reviewer",
                task_type="quality_check",
                params={"threshold": 0.85},
                dependencies=["knowledge_model"],
                priority=4,
                estimated_duration_ms=3000,
            ))

            # Stage 6: 报告生成
            nodes.append(TaskNode(
                node_id="report_gen",
                agent_type="orchestrator",
                task_type="report_generation",
                params={"formats": ["markdown", "json"]},
                dependencies=["quality_review"],
                priority=3,
                estimated_duration_ms=8000,
            ))

            edges = []
            for node in nodes:
                for dep in node.dependencies:
                    edges.append({"from": dep, "to": node.node_id})

            return TaskDAG(nodes=nodes, edges=edges).model_dump()

        return build_task_dag.tool

    def _create_dependency_resolver_tool(self) -> Tool:
        """依赖解析工具"""
        @tool(
            name="resolve_dependencies",
            description="解析任务DAG中各节点的依赖关系，确保无循环依赖",
            schema={
                "type": "object",
                "properties": {
                    "nodes": {"type": "array"},
                    "edges": {"type": "array"},
                },
                "required": ["nodes", "edges"],
            },
            cost="free",
        )
        def resolve_dependencies(nodes: List[Dict], edges: List[Dict]):
            # 拓扑排序检测循环依赖
            in_degree = {n["node_id"]: 0 for n in nodes}
            adj = {n["node_id"]: [] for n in nodes}

            for edge in edges:
                adj[edge["from"]].append(edge["to"])
                in_degree[edge["to"]] = in_degree.get(edge["to"], 0) + 1

            queue = [nid for nid, deg in in_degree.items() if deg == 0]
            sorted_nodes = []

            while queue:
                node = queue.pop(0)
                sorted_nodes.append(node)
                for neighbor in adj[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

            has_cycle = len(sorted_nodes) != len(nodes)
            return {
                "valid": not has_cycle,
                "topological_order": sorted_nodes,
                "has_cycle": has_cycle,
            }

        return resolve_dependencies.tool

    def _create_parallelism_optimizer_tool(self) -> Tool:
        """并行度优化工具"""
        @tool(
            name="optimize_parallelism",
            description="优化任务并行度，在资源约束下最大化吞吐",
            schema={
                "type": "object",
                "properties": {
                    "nodes": {"type": "array"},
                    "max_parallel": {"type": "integer"},
                },
                "required": ["nodes"],
            },
            cost="free",
        )
        def optimize_parallelism(nodes: List[Dict], max_parallel: int = 5):
            # 按拓扑层级分组，同层可并行
            levels: Dict[int, List[str]] = {}
            for node in nodes:
                level = len(node.get("dependencies", []))
                levels.setdefault(level, []).append(node["node_id"])

            parallel_batches = []
            for level, node_ids in sorted(levels.items()):
                for i in range(0, len(node_ids), max_parallel):
                    parallel_batches.append(node_ids[i:i + max_parallel])

            batch_count = len(parallel_batches)
            speedup = f"{len(nodes) / batch_count:.1f}x" if batch_count > 0 else "N/A"
            return {
                "batches": parallel_batches,
                "total_batches": batch_count,
                "estimated_speedup": speedup,
            }

        return optimize_parallelism.tool

    async def run(self, state: SharedState, sources_config: Dict[str, Any] = None) -> SharedState:
        """执行规划逻辑"""
        state.current_stage = "planning"

        # 从配置读取数据源
        sources = []
        if sources_config:
            for rss_url in sources_config.get("rss", []):
                sources.append(f"rss://{rss_url}")
            for web_url in sources_config.get("web", []):
                sources.append(f"web://{web_url}")
        if not sources:
            sources = ["rss://tech", "web://news"]

        # Step 1: 构建任务DAG
        dag_result = self.tools["build_task_dag"].func(
            intent=state.intent,
            data_sources=sources,
        )
        state.plan = TaskDAG(**dag_result)
        self._log_action(state, "build_dag", {"node_count": len(state.plan.nodes)})

        # Step 2: 依赖校验
        dep_result = self.tools["resolve_dependencies"].func(
            nodes=[n.model_dump() for n in state.plan.nodes],
            edges=state.plan.edges,
        )
        self._log_action(state, "resolve_deps", {"valid": dep_result.get("valid")})

        # Step 3: 并行度优化
        opt_result = self.tools["optimize_parallelism"].func(
            nodes=[n.model_dump() for n in state.plan.nodes],
            max_parallel=5,
        )
        self._log_action(state, "optimize_parallel", {"batches": opt_result.get("total_batches", 0)})

        state.current_stage = "planning_complete"
        logger.info(
            f"[Planner] DAG构建完成 → {len(state.plan.nodes)} 个节点, "
            f"拓扑有效: {dep_result.get('data', {}).get('valid')}"
        )

        return state
