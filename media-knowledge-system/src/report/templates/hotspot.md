# {{报告标题}}

**生成时间**：{{timestamp}} | **覆盖时段**：{{coverage_start}} ~ {{coverage_end}}
**数据来源**：{{source_count}} 个来源，共 {{article_count}} 篇文章

---

## 一、热点事件 Top-N

| 排名 | 事件 | 热度分 | 关联实体 | 首次报道 |
|------|------|--------|----------|----------|
{{#each hot_events}}
| {{rank}} | {{event_name}} | {{heat_score}} | {{related_entities}} | {{first_reported}} |
{{/each}}

## 二、关键实体网络

{{network_description}}

### 实体关系图描述

{{graph_description}}

## 三、事件演化时间线

{{timeline}}

## 四、数据来源与置信度说明

| 数据源 | 文章数 | 实体贡献 | 平均置信度 |
|--------|--------|----------|------------|
{{#each sources}}
| {{source_name}} | {{article_count}} | {{entity_count}} | {{avg_confidence}} |
{{/each}}

## 五、不确定性说明

{{uncertainty_notes}}

---

*本报告由多智能体协同系统自动生成*
