"""
文本分块器

将长文本切分为适合LLM处理的块，支持：
- 固定长度分块（滑动窗口）
- 按段落语义分块
- 按句子分块（保持句子完整性）
"""
from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


class TextChunker:
    """文本分块器"""

    def __init__(
        self,
        max_chunk_size: int = 2000,
        overlap: int = 200,
        strategy: str = "sentence",
    ):
        """
        Args:
            max_chunk_size: 每块最大字符数
            overlap: 块间重叠字符数
            strategy: 分块策略 — "fixed" / "sentence" / "paragraph"
        """
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap
        self.strategy = strategy

    def chunk(self, text: str) -> List[str]:
        """将文本分为多个块"""
        if len(text) <= self.max_chunk_size:
            return [text]

        if self.strategy == "paragraph":
            return self._chunk_by_paragraph(text)
        elif self.strategy == "sentence":
            return self._chunk_by_sentence(text)
        else:
            return self._chunk_fixed(text)

    def _chunk_fixed(self, text: str) -> List[str]:
        """固定大小分块（带重叠）"""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.max_chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - self.overlap
        return chunks

    def _chunk_by_sentence(self, text: str) -> List[str]:
        """按句子分块，尽量保持句子完整"""
        sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= self.max_chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # 如果单个句子超过块大小，不得不截断
                if len(sentence) > self.max_chunk_size:
                    sub_chunks = self._chunk_fixed(sentence)
                    chunks.extend(sub_chunks[:-1])
                    current_chunk = sub_chunks[-1] if sub_chunks else ""
                else:
                    current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _chunk_by_paragraph(self, text: str) -> List[str]:
        """按段落分块"""
        paragraphs = text.split("\n\n")
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= self.max_chunk_size:
                current_chunk = (current_chunk + "\n\n" + para).strip()
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                if len(para) > self.max_chunk_size:
                    sub_chunks = self._chunk_by_sentence(para)
                    chunks.extend(sub_chunks[:-1])
                    current_chunk = sub_chunks[-1] if sub_chunks else ""
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def chunk_with_metadata(self, text: str, doc_id: str = "") -> List[dict]:
        """分块并附带元数据"""
        chunks = self.chunk(text)
        return [
            {
                "doc_id": doc_id,
                "chunk_index": i,
                "chunk_count": len(chunks),
                "text": chunk,
                "char_count": len(chunk),
            }
            for i, chunk in enumerate(chunks)
        ]
