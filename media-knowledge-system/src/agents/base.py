"""
Agent 基类与 Tool 工具框架

每个Agent遵循 "LLM决策 + tool_call执行" 模式：
1. Agent接收SharedState
2. LLM分析状态，决策调用哪些工具
3. 执行工具，获取结果
4. LLM综合结果，更新State
5. 返回更新后的State
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from .state import SharedState

logger = logging.getLogger(__name__)


class Tool:
    """工具定义 —— 封装为 OpenAI Function Calling 兼容Schema"""

    def __init__(
        self,
        func: Callable,
        name: str,
        description: str,
        schema: Optional[Dict[str, Any]] = None,
        cost: str = "normal",
    ):
        self._raw_func = func
        self.func = self._wrap(func)
        self.name = name
        self.description = description
        self.schema = schema or {}
        self.cost = cost  # free / low / normal / high

    @staticmethod
    def _wrap(func: Callable) -> Callable:
        """包装原始函数，统一返回 {"status": ..., "data": ...} 格式，支持同步和异步函数"""
        import asyncio

        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(**kwargs):
                try:
                    result = await func(**kwargs)
                    return {"status": "success", "data": result}
                except Exception as e:
                    logger.error(f"Tool [{func.__name__}] execution failed: {e}")
                    return {"status": "error", "error": str(e)}
            return async_wrapper
        else:
            @wraps(func)
            def wrapper(**kwargs):
                try:
                    result = func(**kwargs)
                    return {"status": "success", "data": result}
                except Exception as e:
                    logger.error(f"Tool [{func.__name__}] execution failed: {e}")
                    return {"status": "error", "error": str(e)}
            return wrapper

    def to_openai_schema(self) -> Dict[str, Any]:
        """生成 OpenAI Function Calling Schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"[cost:{self.cost}] {self.description}",
                "parameters": self.schema,
            },
        }

    def __call__(self, **kwargs) -> Dict[str, Any]:
        """直接调用工具（与 func 行为一致）"""
        return self.func(**kwargs)

    def __repr__(self):
        return f"Tool(name={self.name}, cost={self.cost})"


def tool(
    name: str,
    description: str,
    schema: Optional[Dict[str, Any]] = None,
    cost: str = "normal",
):
    """工具装饰器 —— 将普通函数包装为Tool对象"""
    def decorator(func: Callable):
        tool_obj = Tool(func, name, description, schema, cost)

        @wraps(func)
        def wrapper(**kwargs):
            return tool_obj.func(**kwargs)

        wrapper.tool = tool_obj
        return wrapper
    return decorator


class BaseAgent:
    """
    Agent 基类

    子类必须实现：
    - run(state: SharedState) -> SharedState

    子类可选覆盖：
    - _select_tools(state) -> List[Tool]: 自定义工具选择逻辑
    """

    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        tools: Optional[List[Tool]] = None,
        llm_client: Any = None,
    ):
        self.name = name
        self.role = role
        self.goal = goal
        self.tools: Dict[str, Tool] = {}
        self.llm_client = llm_client

        for t in (tools or []):
            self.register_tool(t)

    def register_tool(self, tool_obj: Tool):
        """注册工具到Agent的工具箱"""
        self.tools[tool_obj.name] = tool_obj
        logger.debug(f"[{self.name}] 注册工具: {tool_obj.name} (cost={tool_obj.cost})")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """获取所有工具的OpenAI Function Calling Schema列表"""
        return [t.to_openai_schema() for t in self.tools.values()]

    def get_tools_by_cost(self, max_cost: str) -> List[Tool]:
        """按成本过滤工具"""
        cost_order = {"free": 0, "low": 1, "normal": 2, "high": 3}
        max_level = cost_order.get(max_cost, 3)
        return [t for t in self.tools.values() if cost_order.get(t.cost, 3) <= max_level]

    def _log_action(self, state: SharedState, action: str, result: Dict[str, Any]):
        """记录Agent执行动作到State日志"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": self.name,
            "role": self.role,
            "action": action,
            "status": result.get("status", "unknown"),
            "summary": {k: v for k, v in result.items() if k != "status"},
        }
        state.execution_log.append(entry)
        logger.info(f"[{self.name}] {action} → {result.get('status')}")

    async def run(self, state: SharedState) -> SharedState:
        """
        核心执行方法 —— 子类必须实现

        Args:
            state: 当前共享状态

        Returns:
            更新后的共享状态
        """
        raise NotImplementedError(f"{self.__class__.__name__}.run() must be implemented")

    def __repr__(self):
        registered = ", ".join(self.tools.keys()) if self.tools else "none"
        return f"<{self.name} | {self.role} | tools=[{registered}]>"
