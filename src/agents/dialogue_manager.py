"""
对话管理器

负责：
- 多轮对话历史维护
- 知识图谱上下文检索
- 模糊问题检测与追问
- 答案溯源
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 模糊问题的关键词
FUZZY_KEYWORDS = [
    "那个", "那个呢", "怎么样", "然后呢", "什么情况",
    "具体呢", "还有吗", "说说", "聊聊", "讲讲",
    "那个东西", "它", "他呢", "她呢",
]

# 常见停用词（不应作为实体名去检索）
STOPWORDS = {
    "什么", "怎么", "为什么", "哪些", "如何", "可以", "是否", "有没有",
    "请问", "你好", "谢谢", "最近", "最新", "之前", "关于", "方面",
    "情况", "新闻", "消息", "事情", "事件", "问题", "行业", "领域",
    "发展", "变化", "趋势", "热点",
}


class DialogueManager:
    """对话管理器 —— 管理多轮对话、知识检索、追问"""

    def __init__(
        self,
        llm_client: Any,
        graph_store: Any = None,
        prompt_manager: Any = None,
    ):
        self.llm = llm_client
        self.graph = graph_store
        self.pm = prompt_manager

        # 对话历史：干净的对话消息（system + user/assistant 对）
        # 注意：这里只存原始用户消息，不存拼接了上下文的长 prompt
        self.history: List[Dict[str, str]] = []
        # 仅 user/assistant 的对话轮次（用于 UI 展示）
        self.turns: List[Dict[str, str]] = []

        # 追问计数（同一问题连续追问不超过2次）
        self._followup_count = 0
        self._last_topic: Optional[str] = None

        # 加载图谱数据
        if self.graph:
            try:
                self.graph.load_demo_data()
            except Exception as e:
                logger.warning(f"加载演示数据失败: {e}")

        # 初始化 system prompt
        self._init_system_prompt()

    def _init_system_prompt(self):
        """从模板加载 system prompt"""
        system_text = ""
        if self.pm:
            try:
                template = self.pm.get("dialogue")
                system_text = template.get("system", "")
            except Exception:
                pass

        if not system_text:
            system_text = (
                "你是一个专业的媒体领域知识问答助手。"
                "当用户提问时，我会提供知识图谱中的相关信息作为上下文，"
                "请你基于这些上下文回答用户的问题。"
                "如果上下文中有相关信息，引用具体实体名称和关系。"
                "如果上下文不足，请说明哪些方面无法确认，不要编造信息。"
                "回答使用中文，简洁清晰。"
            )

        self.history.append({"role": "system", "content": system_text})

    def reset(self):
        """清空对话历史，重新开始"""
        self.history.clear()
        self.turns.clear()
        self._followup_count = 0
        self._last_topic = None
        self._init_system_prompt()

    def get_history(self) -> List[Dict[str, str]]:
        """获取对话轮次（仅 user/assistant）"""
        return list(self.turns)

    # ------------------------------------------------------------------
    # 核心入口
    # ------------------------------------------------------------------

    async def chat(self, user_message: str) -> Dict[str, Any]:
        """
        处理一轮用户对话

        Returns:
            {
                "answer": str,
                "sources": List[Dict],   # 引用的知识来源
                "need_followup": bool,
                "followup_question": str | None,
            }
        """
        user_message = user_message.strip()
        if not user_message:
            return {"answer": "请输入您的问题。", "sources": [], "need_followup": False, "followup_question": None}

        # 1. 模糊检测
        fuzzy = self._check_fuzzy(user_message)
        if fuzzy["is_fuzzy"] and self._followup_count < 2:
            self._followup_count += 1
            followup = self._generate_followup(user_message, fuzzy["reason"])
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": followup})
            self.turns.append({"role": "user", "content": user_message})
            self.turns.append({"role": "assistant", "content": followup})
            return {
                "answer": followup,
                "sources": [],
                "need_followup": True,
                "followup_question": followup,
            }

        # 2. 重置追问计数
        self._followup_count = 0

        # 3. 知识检索
        context, sources = self._retrieve_knowledge(user_message)

        # 4. 构建调用消息列表
        #    关键：history 保持干净，只在本次调用时临时拼接上下文
        call_messages = list(self.history)  # 拷贝历史

        if context:
            augmented_prompt = (
                f"知识图谱上下文：\n{context}\n\n"
                f"---\n\n"
                f"用户问题：{user_message}\n\n"
                f"请基于以上知识图谱上下文回答。如果上下文信息不足，补充说明。"
            )
        else:
            augmented_prompt = (
                f"用户问题：{user_message}\n\n"
                f"（知识图谱中未找到直接相关信息，请基于通用知识回答，并告知用户。）"
            )

        call_messages.append({"role": "user", "content": augmented_prompt})

        # 5. 调用 LLM（传入临时消息列表，不污染 self.history）
        answer = self.llm.chat(call_messages)

        # 6. 只将原始用户消息和回答加入历史
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": answer})
        self.turns.append({"role": "user", "content": user_message})
        self.turns.append({"role": "assistant", "content": answer})

        # 7. 更新最近话题
        self._last_topic = user_message

        return {
            "answer": answer,
            "sources": sources,
            "need_followup": False,
            "followup_question": None,
        }

    # ------------------------------------------------------------------
    # 模糊检测
    # ------------------------------------------------------------------

    def _check_fuzzy(self, question: str) -> Dict[str, Any]:
        """
        基于规则检测问题是否模糊

        返回 {"is_fuzzy": bool, "reason": str}
        """
        # 过短（<5字）且无实质内容
        if len(question) < 5 and question in FUZZY_KEYWORDS:
            return {"is_fuzzy": True, "reason": "问题过短且内容模糊"}

        # 包含模糊关键词（精确匹配）
        for kw in FUZZY_KEYWORDS:
            if question == kw or question == kw + "？" or question == kw + "?":
                return {"is_fuzzy": True, "reason": f"问题含模糊词「{kw}」"}

        # 仅指代词（他/她/它/那个）且无上下文
        pronoun_only = re.match(r"^(他|她|它|那个|这个)(呢|？|\?|怎么样)?$", question)
        if pronoun_only:
            return {"is_fuzzy": True, "reason": "问题仅为指代词，缺少具体对象"}

        # 省略主语的追问（如"最新的呢""还有吗"）
        if re.match(r"^(最新|最近|之前|后来|然后|还有|具体)(的呢|呢|吗|吗？|的？|\?)*$", question):
            return {"is_fuzzy": True, "reason": "问题省略了主语，需要明确指代对象"}

        return {"is_fuzzy": False, "reason": ""}

    def _generate_followup(self, question: str, reason: str) -> str:
        """生成追问"""
        # 模板兜足（优先使用，避免无谓的 LLM 调用）
        if self._last_topic:
            return f"您刚才问到了「{self._last_topic}」，能具体说明想了解哪个方面吗？比如相关的时间、人物或事件。"
        return "您的问题我还不太确定具体指什么，能再描述一下吗？比如您想了解哪个人物、公司或事件？"

    def _summarize_history(self) -> str:
        """摘要化对话历史（取最近3轮）"""
        recent = self.turns[-6:] if len(self.turns) > 6 else self.turns
        parts = []
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:100]
            parts.append(f"{role}：{content}")
        return "\n".join(parts) if parts else "（无对话历史）"

    # ------------------------------------------------------------------
    # 知识检索
    # ------------------------------------------------------------------

    def _retrieve_knowledge(self, question: str) -> tuple[str, List[Dict]]:
        """
        从知识图谱中检索与问题相关的上下文

        返回 (context_text, sources_list)
        """
        if not self.graph:
            return "", []

        sources: List[Dict] = []
        context_parts: List[str] = []
        seen_entities: set = set()

        # 从问题中提取可能的实体关键词
        keywords = self._extract_keywords(question)

        for kw in keywords:
            if len(seen_entities) >= 5:
                break

            similar = self.graph.find_similar_entities(kw, limit=3)
            for ent in similar:
                ent_name = ent.get("name", "")
                if ent_name in seen_entities:
                    continue
                seen_entities.add(ent_name)

                sources.append({
                    "type": "entity",
                    "name": ent_name,
                    "entity_type": ent.get("type", ""),
                    "mention_count": ent.get("mention_count", 0),
                    "confidence": ent.get("confidence", 0),
                })
                context_parts.append(
                    f"【实体】{ent_name}（{ent.get('type', '未知')}，"
                    f"提及{ent.get('mention_count', 0)}次，"
                    f"置信度{ent.get('confidence', 0):.2f}）"
                )

                # 获取子图关系
                subgraph = self.graph.get_subgraph(ent_name, depth=1)
                for node in subgraph.get("nodes", [])[:5]:
                    if node["name"] not in seen_entities:
                        context_parts.append(f"  ├─ 关联：{node['name']}（{node.get('type', '')}）")
                for edge in subgraph.get("edges", [])[:5]:
                    sources.append({
                        "type": "relation",
                        "relation_type": edge.get("relation_type", ""),
                        "confidence": edge.get("confidence", 0),
                    })
                    context_parts.append(
                        f"  └─ 关系类型：{edge.get('relation_type', 'related_to')} "
                        f"（置信度{edge.get('confidence', 0):.2f}）"
                    )

        context_text = "\n".join(context_parts) if context_parts else ""
        return context_text, sources

    def _extract_keywords(self, text: str) -> List[str]:
        """
        从文本中提取可能用于知识检索的关键词

        策略：
        1. 提取连续中文字符（2字以上）
        2. 过滤停用词
        3. 保留英文专有名词（如 GPT-5, OpenAI）
        """
        keywords = []

        # 中文关键词
        cn_words = re.findall(r"[一-鿿]{2,}", text)
        for w in cn_words:
            if w not in STOPWORDS and len(w) <= 10:
                keywords.append(w)

        # 英文专有名词（大写开头的连续英文/数字/连字符组合）
        en_words = re.findall(r"[A-Z][A-Za-z0-9\-_.]+", text)
        keywords.extend(en_words)

        # 也匹配全部大写的缩写（如 AI, CEO）
        en_acronyms = re.findall(r"\b[A-Z]{2,}\b", text)
        keywords.extend(en_acronyms)

        return keywords

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_qa_prompt_with_context(self, question: str, context: str) -> str:
        """构建带知识上下文的问答 prompt"""
        if self.pm:
            try:
                template = self.pm.get("dialogue")
                qa_template = template.get("qa_with_context", "")
                if qa_template:
                    return qa_template.format(context=context, question=question)
            except Exception:
                pass

        return (
            f"知识图谱上下文：\n{context}\n\n"
            f"---\n\n"
            f"用户问题：{question}\n\n"
            f"请基于以上知识图谱上下文回答。"
        )

    def _build_qa_prompt_without_context(self, question: str) -> str:
        """构建无知识上下文的问答 prompt"""
        if self.pm:
            try:
                template = self.pm.get("dialogue")
                qa_template = template.get("qa_without_context", "")
                if qa_template:
                    return qa_template.format(question=question)
            except Exception:
                pass

        return (
            f"用户问题：{question}\n\n"
            f"（知识图谱中未找到直接相关信息，请基于通用知识回答。）"
        )
