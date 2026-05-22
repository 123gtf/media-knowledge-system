# 数据质量审核报告

**生成时间**：{{timestamp}}
**审核范围**：{{review_scope}}

---

## 一、整体质量概览

| 指标 | 值 | 状态 |
|------|-----|------|
| 综合质量分 | {{overall_score}} | {{overall_status}} |
| 审核通过率 | {{pass_rate}} | |
| 严重问题数 | {{critical_count}} | |
| 警告数 | {{warning_count}} | |

## 二、各阶段质量详情

{{#each stage_quality}}
### {{stage_name}}

| 指标 | 值 |
|------|-----|
| 置信度 | {{confidence}} |
| 数据完整性 | {{completeness}} |
| 问题数 | {{issue_count}} |

{{#if issues}}
| 类型 | 严重度 | 描述 | 建议 |
|------|--------|------|------|
{{#each issues}}
| {{type}} | {{severity}} | {{description}} | {{suggestion}} |
{{/each}}
{{/if}}
{{/each}}

## 三、修正建议汇总

{{correction_summary}}

## 四、数据源可靠性评估

| 数据源 | 文章数 | 平均质量分 | 可靠性评级 |
|--------|--------|------------|------------|
{{#each source_reliability}}
| {{source}} | {{count}} | {{quality}} | {{reliability}} |
{{/each}}

---

*本报告由多智能体协同系统自动生成*
