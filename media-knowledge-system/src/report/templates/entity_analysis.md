# {{实体名称}} 深度分析报告

**生成时间**：{{timestamp}}
**分析范围**：{{coverage_start}} ~ {{coverage_end}}

---

## 一、实体画像

| 属性 | 值 |
|------|-----|
| 实体名称 | {{entity_name}} |
| 实体类型 | {{entity_type}} |
| 首次发现 | {{first_seen}} |
| 最近提及 | {{last_seen}} |
| 总提及次数 | {{mention_count}} |
| 别名 | {{aliases}} |

## 二、关联网络

### 直接关联实体 Top-10

| 关联实体 | 关系类型 | 关系强度 | 证据 |
|----------|----------|----------|------|
{{#each related_entities}}
| {{name}} | {{relation_type}} | {{strength}} | {{evidence_summary}} |
{{/each}}

### 二度关联实体

{{second_degree_entities}}

## 三、相关事件时间线

{{event_timeline}}

## 四、情感/倾向分析

{{sentiment_analysis}}

## 五、数据源覆盖

| 来源 | 文章数 | 时间段 |
|------|--------|--------|
{{#each sources}}
| {{source_name}} | {{count}} | {{time_range}} |
{{/each}}

---

*本报告由多智能体协同系统自动生成*
