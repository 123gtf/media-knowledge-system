"""
报告生成器

根据SharedState中的分析结果，生成多类型分析报告：
- 热点事件报告 (HOTSPOT)
- 实体深度分析报告 (ENTITY_ANALYSIS)
- 主题趋势报告 (TOPIC_TREND)
- 数据质量报告 (DATA_QUALITY)

输出格式：Markdown + JSON
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReportGenerator:
    """分析报告生成器"""

    REPORT_TYPES = ["HOTSPOT", "ENTITY_ANALYSIS", "TOPIC_TREND", "DATA_QUALITY"]

    def __init__(self, llm_client: Any = None, output_dir: str = "./output/reports"):
        self.llm_client = llm_client
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        report_type: str,
        state_data: Dict[str, Any],
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成分析报告

        Args:
            report_type: 报告类型
            state_data: SharedState的数据字典
            title: 报告标题（可选）

        Returns:
            {"report_id": str, "markdown": str, "json": dict, "path": str}
        """
        if report_type not in self.REPORT_TYPES:
            raise ValueError(f"未知报告类型: {report_type}，可选: {self.REPORT_TYPES}")

        report_id = f"rpt_{report_type.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        title = title or self._default_title(report_type)

        # 生成Markdown
        if report_type == "HOTSPOT":
            markdown = self._generate_hotspot_report(title, state_data)
        elif report_type == "ENTITY_ANALYSIS":
            markdown = self._generate_entity_analysis_report(title, state_data)
        elif report_type == "TOPIC_TREND":
            markdown = self._generate_topic_trend_report(title, state_data)
        else:
            markdown = self._generate_quality_report(title, state_data)

        # JSON格式
        json_data = self._build_json_report(report_id, report_type, title, state_data)

        # 保存文件
        md_path = self.output_dir / f"{report_id}.md"
        json_path = self.output_dir / f"{report_id}.json"

        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"报告已生成: {md_path}")

        return {
            "report_id": report_id,
            "title": title,
            "report_type": report_type,
            "markdown": markdown,
            "json": json_data,
            "markdown_path": str(md_path),
            "json_path": str(json_path),
        }

    def _generate_hotspot_report(self, title: str, data: Dict) -> str:
        """生成热点事件报告"""
        entities = data.get("extracted_entities", [])
        relations = data.get("extracted_relations", [])
        events = data.get("extracted_events", [])

        # 按提及次数排序
        entity_freq: Dict[str, int] = {}
        for e in entities:
            name = e.get("name", "")
            entity_freq[name] = entity_freq.get(name, 0) + 1

        top_entities = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)[:10]

        entity_rows = "\n".join(
            f"| {name} | {count} | |"
            for name, count in top_entities
        )

        event_rows = "\n".join(
            f"| {e.get('name', '')} | {e.get('event_type', e.get('type', ''))} | "
            f"{e.get('confidence', 0):.0%} |"
            for e in events[:10]
        )

        relation_rows = "\n".join(
            f"| {r.get('head', '')} | {r.get('relation', r.get('relation_type', ''))} | "
            f"{r.get('tail', '')} | {r.get('confidence', 0):.0%} |"
            for r in relations[:15]
        )

        return f"""# {title}

**生成时间**: {datetime.now().isoformat()}
**报告类型**: 热点事件分析

---

## 一、热点实体 Top-10

| 实体名称 | 提及次数 | 趋势 |
|----------|----------|------|
{entity_rows}

## 二、热点事件

| 事件名称 | 事件类型 | 置信度 |
|----------|----------|--------|
{event_rows}

## 三、关键关系网络

| 主体 | 关系 | 宾语 | 置信度 |
|------|------|------|--------|
{relation_rows}

## 四、统计摘要

- 总文章数: {data.get('raw_articles_count', len(data.get('raw_documents', [])))}
- 总实体数: {len(entities)}
- 总关系数: {len(relations)}
- 总事件数: {len(events)}
- 平均置信度: {self._avg_confidence(data):.2%}

---

*本报告由多智能体协同系统自动生成*
"""

    def _generate_entity_analysis_report(self, title: str, data: Dict) -> str:
        """生成实体深度分析报告"""
        entities = data.get("extracted_entities", [])
        relations = data.get("extracted_relations", [])

        entity_details = ""
        for e in entities[:5]:
            name = e.get("name", "")
            etype = e.get("type", "")
            conf = e.get("confidence", 0)

            # 找该实体参与的关系
            related_rels = [
                r for r in relations
                if r.get("head") == name or r.get("tail") == name
            ]
            rel_list = "\n".join(
                f"  - {r.get('head')} → [{r.get('relation', '')}] → {r.get('tail')}"
                for r in related_rels[:5]
            )

            entity_details += f"""
### {name} ({etype})

- 置信度: {conf:.2%}
- 关联关系数: {len(related_rels)}
{rel_list}
"""

        return f"""# {title}

**生成时间**: {datetime.now().isoformat()}

---

{entity_details}

## 关联网络分析

| 维度 | 数值 |
|------|------|
| 独立实体数 | {len(entities)} |
| 关系总数 | {len(relations)} |
| 平均关联度 | {(len(relations) / max(len(entities), 1)):.1f} |

---

*本报告由多智能体协同系统自动生成*
"""

    def _generate_topic_trend_report(self, title: str, data: Dict) -> str:
        """生成主题趋势报告"""
        entities = data.get("extracted_entities", [])
        events = data.get("extracted_events", [])

        # 按类型分组
        by_type: Dict[str, List] = {}
        for e in entities:
            etype = e.get("type", "OTHER")
            by_type.setdefault(etype, []).append(e.get("name", ""))

        type_summary = "\n".join(
            f"| {etype} | {len(names)} | {', '.join(names[:5])} |"
            for etype, names in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True)
        )

        return f"""# {title}

**生成时间**: {datetime.now().isoformat()}

---

## 一、实体类型分布

| 类型 | 数量 | 代表实体 |
|------|------|----------|
{type_summary}

## 二、事件趋势

| 事件 | 类型 | 置信度 |
|------|------|--------|
{chr(10).join(f"| {e.get('name', '')} | {e.get('event_type', '')} | {e.get('confidence', 0):.0%} |" for e in events[:15])}

## 三、统计总览

- 实体总数: {len(entities)}
- 事件总数: {len(events)}
- 覆盖类型: {len(by_type)} 类

---

*本报告由多智能体协同系统自动生成*
"""

    def _generate_quality_report(self, title: str, data: Dict) -> str:
        """生成数据质量报告"""
        confidence_scores = data.get("confidence_scores", {})
        review_flags = data.get("review_flags", [])

        score_rows = "\n".join(
            f"| {stage} | {score:.2%} |"
            for stage, score in confidence_scores.items()
        )

        flag_rows = "\n".join(
            f"| {f.get('type', '')} | {f.get('severity', '')} | {f.get('target', '')} | {f.get('description', '')} |"
            for f in review_flags[:20]
        )

        return f"""# {title}

**生成时间**: {datetime.now().isoformat()}

---

## 一、各阶段置信度

| 阶段 | 置信度 |
|------|--------|
{score_rows}

## 二、质检标记

| 类型 | 严重度 | 目标 | 描述 |
|------|--------|------|------|
{flag_rows}

## 三、统计

- 审核标记总数: {len(review_flags)}
- 严重问题: {len([f for f in review_flags if f.get('severity') == 'critical'])}
- 警告: {len([f for f in review_flags if f.get('severity') == 'warning'])}
- 平均置信度: {self._avg_confidence(data):.2%}

---

*本报告由多智能体协同系统自动生成*
"""

    def _build_json_report(
        self,
        report_id: str,
        report_type: str,
        title: str,
        data: Dict,
    ) -> Dict[str, Any]:
        """构建JSON格式报告"""
        return {
            "report_id": report_id,
            "report_type": report_type,
            "title": title,
            "generated_at": datetime.now().isoformat(),
            "statistics": {
                "raw_articles": len(data.get("raw_documents", [])),
                "cleaned_articles": len(data.get("cleaned_documents", [])),
                "extracted_entities": len(data.get("extracted_entities", [])),
                "extracted_relations": len(data.get("extracted_relations", [])),
                "extracted_events": len(data.get("extracted_events", [])),
                "review_flags": len(data.get("review_flags", [])),
            },
            "confidence_scores": data.get("confidence_scores", {}),
            "review_flags": data.get("review_flags", []),
            "knowledge_updates": data.get("knowledge_updates", {}),
        }

    @staticmethod
    def _default_title(report_type: str) -> str:
        """默认报告标题"""
        titles = {
            "HOTSPOT": "热点事件分析报告",
            "ENTITY_ANALYSIS": "实体深度分析报告",
            "TOPIC_TREND": "主题趋势分析报告",
            "DATA_QUALITY": "数据质量审核报告",
        }
        return titles.get(report_type, "分析报告")

    @staticmethod
    def _avg_confidence(data: Dict) -> float:
        """计算平均置信度"""
        scores = data.get("confidence_scores", {})
        if not scores:
            return 0.0
        return sum(scores.values()) / len(scores)
