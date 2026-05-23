"""
数据清洗流水线

对采集的原始HTML/文本执行多阶段清洗：
1. HTML标签去除
2. 广告/导航/噪声去除
3. 空白规范化
4. 编码统一(UTF-8)
5. 正文质量评分
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CleaningResult:
    """清洗结果"""
    title: str
    content: str
    clean_summary: str = ""
    word_count: int = 0
    quality_score: float = 0.0
    issues: List[str] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


class DataCleaner:
    """数据清洗流水线"""

    # 常见噪声模式（中文 + 英文）
    NOISE_PATTERNS = [
        # 广告相关
        r"广告|AD\s*$|赞助|推广|Advertisement",
        # 导航相关
        r"相关阅读|推荐阅读|热门推荐|猜你喜欢|Related Articles",
        r"上一篇|下一篇|返回首页|back to top",
        # 社交分享
        r"分享到|分享至|Share on|Tweet|Pin it",
        r"扫码|关注微信|关注微博|Subscribe|Follow us",
        # 版权
        r"Copyright\s*©.*|All Rights Reserved",
        r"版权所有.*|未经许可.*不得转载",
        # 评论区
        r"评论.*|Comments.*|读者留言|网友评论",
        # 脚本残留
        r"function\s*\(.*\)\s*\{.*\}|var\s+\w+\s*=",
        r"document\.\w+|window\.\w+|console\.\w+",
        # CSS残留
        r"\.\w+\s*\{[^}]*\}",
        # 多余空白符号
        r"&nbsp;|&lt;|&gt;|&amp;|&quot;|&#\d+;",
    ]

    def __init__(self, min_content_length: int = 50):
        self.min_content_length = min_content_length
        self._compiled_patterns = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in self.NOISE_PATTERNS]

    def clean(self, raw_html: str, url: str = "", title: str = "") -> CleaningResult:
        """
        清洗原始HTML，返回结构化清洗结果

        Args:
            raw_html: 原始HTML文本
            url: 来源URL（用于域名提取）
            title: 已知标题（可选）

        Returns:
            CleaningResult
        """
        issues = []

        # Stage 1: 去除HTML标签
        text = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)

        # Stage 2: 解码HTML实体
        import html as html_module
        text = html_module.unescape(text)

        # Stage 3: 去除噪声
        for pattern in self._compiled_patterns:
            text = pattern.sub(" ", text)

        # Stage 4: 空白规范化
        text = re.sub(r"\s+", " ", text).strip()
        # 合并被空白打断的中文
        text = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", text)

        # Stage 5: 段落分割与空段过滤
        paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 10]
        text = "\n\n".join(paragraphs)

        # Stage 6: 质量评分
        word_count = len(text)
        quality_score = self._calculate_quality(text, word_count)

        if word_count < self.min_content_length:
            issues.append(f"内容过短: {word_count} 字符")
            quality_score = min(quality_score, 0.3)

        # 生成摘要（取前200字）
        clean_summary = text[:200] + ("..." if len(text) > 200 else "")

        return CleaningResult(
            title=title or self._extract_title_from_text(text),
            content=text,
            clean_summary=clean_summary,
            word_count=word_count,
            quality_score=quality_score,
            issues=issues,
        )

    def _calculate_quality(self, text: str, word_count: int) -> float:
        """计算文本质量分 (0-1)"""
        score = 0.5

        # 长度加分
        if word_count > 5000:
            score += 0.1
        elif word_count > 1000:
            score += 0.05

        # 中英混合正常
        has_chinese = bool(re.search(r"[一-鿿]", text))
        has_english = bool(re.search(r"[a-zA-Z]{3,}", text))
        if has_chinese or has_english:
            score += 0.1

        # 包含结构化信息加分
        if re.search(r"\d{4}年|\d{4}-\d{2}-\d{2}|\d{2}:\d{2}", text):
            score += 0.05
        if re.search(r"[，。！？；：、]", text):
            score += 0.05

        # 疑似代码/垃圾文本减分
        code_indicators = len(re.findall(r"[{}();=<>]", text))
        if code_indicators > 20:
            score -= 0.2

        # 垃圾链接减分
        url_count = len(re.findall(r"https?://", text))
        if url_count > 10:
            score -= 0.1

        return max(0.0, min(1.0, score))

    def _extract_title_from_text(self, text: str) -> str:
        """从正文中提取可能的标题"""
        first_line = text.split("\n")[0].strip()
        if len(first_line) <= 100 and first_line:
            return first_line
        return text[:100]

    def clean_batch(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量清洗"""
        results = []
        for doc in documents:
            result = self.clean(
                raw_html=doc.get("content", ""),
                url=doc.get("url", ""),
                title=doc.get("title", ""),
            )
            if result.quality_score >= 0.3:
                results.append({
                    **doc,
                    "clean_title": result.title,
                    "clean_content": result.content,
                    "clean_summary": result.clean_summary,
                    "word_count": result.word_count,
                    "quality_score": result.quality_score,
                })
        logger.info(f"批量清洗: {len(documents)} → {len(results)} (过滤 {len(documents) - len(results)} 篇)")
        return results
