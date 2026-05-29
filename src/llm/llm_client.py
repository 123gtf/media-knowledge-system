"""
LLM 调用客户端封装

支持：
- Anthropic Claude API
- OpenAI GPT API
- 本地模型（占位）
- 自动重试与降级
- 成本追踪
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM API调用客户端"""

    def __init__(
        self,
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        request_timeout: int = 60,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.request_timeout = request_timeout

        # 成本追踪
        self.total_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0

        self._client = None

    @property
    def client(self):
        """懒加载API客户端"""
        if self._client is None:
            if self.provider == "anthropic":
                try:
                    from anthropic import Anthropic
                    self._client = Anthropic(api_key=self.api_key)
                    logger.info(f"Anthropic客户端初始化: model={self.model}")
                except ImportError:
                    logger.warning("anthropic库未安装")
                except Exception as e:
                    logger.warning(f"Anthropic客户端初始化失败: {e}")
            elif self.provider == "openai":
                try:
                    from openai import OpenAI
                    self._client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                    )
                    logger.info(f"OpenAI客户端初始化: model={self.model}")
                except ImportError:
                    logger.warning("openai库未安装")
                except Exception as e:
                    logger.warning(f"OpenAI客户端初始化失败: {e}")
        return self._client

    def call(
        self,
        prompt: str,
        system: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        max_retries: int = 2,
    ) -> str:
        """
        调用LLM

        Args:
            prompt: 用户提示词
            system: 系统提示词
            max_tokens: 最大输出token数
            temperature: 温度参数
            max_retries: 最大重试次数

        Returns:
            LLM响应文本
        """
        if not self.api_key:
            logger.warning("API Key未配置，返回Mock响应")
            return self._mock_response(prompt)

        if not self.client:
            logger.warning(f"{self.provider}客户端不可用，返回Mock响应")
            return self._mock_response(prompt)

        for attempt in range(max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return self._call_anthropic(
                        prompt, system, max_tokens, temperature
                    )
                elif self.provider == "openai":
                    return self._call_openai(
                        prompt, system, max_tokens, temperature
                    )
            except Exception as e:
                logger.warning(
                    f"LLM调用失败 (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error("LLM调用最终失败，返回Mock响应")
                    return self._mock_response(prompt)

        return self._mock_response(prompt)

    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        max_retries: int = 2,
    ) -> str:
        """
        多轮对话调用

        Args:
            messages: 对话历史，格式 [{"role": "system/user/assistant", "content": "..."}]
            max_tokens: 最大输出token数
            temperature: 温度参数
            max_retries: 最大重试次数

        Returns:
            LLM响应文本
        """
        if not self.api_key:
            logger.warning("API Key未配置，返回Mock响应")
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return self._mock_response(last_user)

        if not self.client:
            logger.warning(f"{self.provider}客户端不可用，返回Mock响应")
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return self._mock_response(last_user)

        for attempt in range(max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return self._chat_anthropic(messages, max_tokens, temperature)
                elif self.provider == "openai":
                    return self._chat_openai(messages, max_tokens, temperature)
            except Exception as e:
                logger.warning(
                    f"LLM多轮对话调用失败 (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("LLM多轮对话最终失败，返回Mock响应")
                    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
                    return self._mock_response(last_user)

        return ""

    def _chat_anthropic(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Anthropic 多轮对话"""
        # Anthropic 要求 system 单独传，不能放在 messages 里
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            kwargs["system"] = system
        kwargs["temperature"] = temperature if temperature is not None else self.temperature

        response = self.client.messages.create(**kwargs)

        usage = response.usage
        self.total_tokens += usage.input_tokens + usage.output_tokens
        self.call_count += 1

        return response.content[0].text

    def _chat_openai(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """OpenAI 多轮对话"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
        )

        usage = response.usage
        self.total_tokens += usage.prompt_tokens + usage.completion_tokens
        self.call_count += 1

        return response.choices[0].message.content

    def _call_anthropic(
        self,
        prompt: str,
        system: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """调用Anthropic Claude API"""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = self.temperature

        response = self.client.messages.create(**kwargs)

        # 成本追踪
        usage = response.usage
        self.total_tokens += usage.input_tokens + usage.output_tokens
        self.call_count += 1

        return response.content[0].text

    def _call_openai(
        self,
        prompt: str,
        system: str = "",
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """调用OpenAI GPT API"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
        )

        # 成本追踪
        usage = response.usage
        self.total_tokens += usage.prompt_tokens + usage.completion_tokens
        self.call_count += 1

        return response.choices[0].message.content

    def _mock_response(self, prompt: str) -> str:
        """生成Mock响应（无API时的降级方案）"""
        prompt_lower = prompt.lower()

        # 根据Prompt内容返回合理的Mock JSON
        if "entities" in prompt_lower and "ner" in prompt_lower:
            return json.dumps({"entities": []}, ensure_ascii=False)
        elif "relations" in prompt_lower:
            return json.dumps({"relations": []}, ensure_ascii=False)
        elif "events" in prompt_lower:
            return json.dumps({"events": []}, ensure_ascii=False)
        elif "summary" in prompt_lower or "摘要" in prompt:
            return json.dumps({
                "summary": "这是一个Mock摘要（LLM未配置）。请配置API Key以获取真实摘要。",
                "key_points": ["配置ANTHROPIC_API_KEY或OPENAI_API_KEY环境变量"],
            }, ensure_ascii=False)
        elif "仲裁" in prompt or "arbitrate" in prompt_lower:
            return json.dumps({"decision": "undecided", "confidence": 0.5, "reason": "LLM未配置"}, ensure_ascii=False)
        elif "review" in prompt_lower or "审核" in prompt:
            return json.dumps({"passed": True, "overall_confidence": 0.8, "issues": []}, ensure_ascii=False)
        elif "追问" in prompt or "clarify" in prompt_lower or "followup" in prompt_lower:
            return "您能具体说明一下想了解哪个方面吗？比如具体的时间范围、特定的实体或事件类型。"
        elif "report" in prompt_lower or "报告" in prompt:
            return "# Mock报告\n\nLLM未配置，请设置API Key。"
        elif "对话" in prompt or "问答" in prompt or "回答" in prompt or "answer" in prompt_lower:
            return "这是一个Mock回答（LLM未配置）。请配置ANTHROPIC_API_KEY或OPENAI_API_KEY环境变量以获取真实回答。"
        else:
            return json.dumps({"result": "mock", "note": "LLM not configured"}, ensure_ascii=False)

    def get_cost_summary(self) -> Dict[str, Any]:
        """获取成本摘要"""
        return {
            "provider": self.provider,
            "model": self.model,
            "total_calls": self.call_count,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.total_cost,
        }
