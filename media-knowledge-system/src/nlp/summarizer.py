"""
摘要生成模块

支持两种策略：
- LLM生成式摘要（高质量，有API成本）
- 抽取式摘要（降级方案，基于TextRank/词频）
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TextSummarizer:
    """文本摘要生成器"""

    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client

    def summarize(
        self,
        text: str,
        max_length: int = 200,
        strategy: str = "auto",
    ) -> Dict[str, Any]:
        """
        生成文本摘要

        Args:
            text: 输入文本
            max_length: 摘要最大长度（字）
            strategy: 策略 — "llm" / "extractive" / "auto"(自动选择)

        Returns:
            {"summary": "...", "key_points": [...], "method": "llm|extractive"}
        """
        if len(text) <= max_length:
            return {
                "summary": text,
                "key_points": [],
                "method": "identity",
            }

        if strategy == "auto":
            strategy = "llm" if self.llm_client else "extractive"

        if strategy == "llm" and self.llm_client:
            return self._llm_summarize(text, max_length)
        else:
            return self._extractive_summarize(text, max_length)

    def _llm_summarize(self, text: str, max_length: int) -> Dict[str, Any]:
        """LLM生成式摘要"""
        prompt = f"""请为以下文本生成不超过{max_length}字的摘要，保留5W1H关键要素。

文本：
{text[:3000]}

请只输出JSON：
{{{{
  "summary": "摘要内容",
  "key_points": ["关键点1", "关键点2", "关键点3"]
}}}}"""

        try:
            response = self.llm_client.call(prompt)
            result = json.loads(response) if isinstance(response, str) else response
            return {
                "summary": result.get("summary", text[:max_length]),
                "key_points": result.get("key_points", []),
                "method": "llm",
            }
        except Exception as e:
            logger.warning(f"LLM摘要失败: {e}，降级为抽取式")
            return self._extractive_summarize(text, max_length)

    def _extractive_summarize(self, text: str, max_length: int) -> Dict[str, Any]:
        """抽取式摘要 —— 基于句子重要性评分"""
        sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

        if not sentences:
            return {"summary": text[:max_length], "key_points": [], "method": "extractive"}

        if len(sentences) <= 3:
            summary = "。".join(sentences)
            return {
                "summary": summary[:max_length],
                "key_points": sentences[:3],
                "method": "extractive",
            }

        # 计算句子重要性
        scores = self._score_sentences(sentences, text)

        # 选Top-N句子
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        summary_sentences = []
        total_len = 0
        for idx, score in ranked:
            sent = sentences[idx]
            if total_len + len(sent) <= max_length:
                summary_sentences.append((idx, sent))
                total_len += len(sent)
            if total_len >= max_length:
                break

        # 按原文顺序排列
        summary_sentences.sort(key=lambda x: x[0])
        summary = "。".join(s[1] for s in summary_sentences) + "。"

        # 关键点：得分最高的3句
        key_points = [s[1] for s in sorted(ranked[:3], key=lambda x: x[1], reverse=True)]

        return {
            "summary": summary[:max_length],
            "key_points": key_points,
            "method": "extractive",
        }

    def _score_sentences(self, sentences: List[str], full_text: str) -> List[float]:
        """计算句子重要性评分（简化的TextRank思路）"""
        # 词频统计
        words = re.findall(r"[一-鿿]+|[a-zA-Z]{2,}", full_text)
        word_freq = Counter(words)

        # 位置权重（开头和结尾更重要）
        n = len(sentences)
        position_weights = []
        for i in range(n):
            if i == 0:
                position_weights.append(1.5)
            elif i == n - 1:
                position_weights.append(1.3)
            elif i < n * 0.2:
                position_weights.append(1.2)
            else:
                position_weights.append(0.9)

        # 综合评分
        scores = []
        for i, sent in enumerate(sentences):
            sent_words = re.findall(r"[一-鿿]+|[a-zA-Z]{2,}", sent)
            if not sent_words:
                scores.append(0.0)
                continue

            # TF权重
            tf_score = sum(word_freq.get(w, 0) for w in sent_words) / len(sent_words)

            # 实体密度加分（包含专有名词的句子更重要）
            proper_nouns = len(re.findall(r"[A-Z][a-z]+|[一-鿿]{2,4}(公司|集团|政府|大学)", sent))
            entity_bonus = min(0.3, proper_nouns * 0.05)

            # 数字/时间加分
            has_number = 0.1 if re.search(r"\d+", sent) else 0

            score = tf_score * position_weights[i] + entity_bonus + has_number
            scores.append(score)

        return scores
