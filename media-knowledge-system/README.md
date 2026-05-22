# 多智能体协同的媒体数据分析与知识库构建系统

## 项目概述

本项目设计并实现了一套以**多智能体协同编排**为核心的媒体数据分析与知识库构建系统。系统中 6 个专业化 Agent 通过 **tool_call（函数调用）** 机制按需调用外部工具，围绕共享状态协同工作，对多源媒体数据进行采集、清洗、结构化抽取与关联融合，最终产出**结构化数据分析报告**与**可查询的领域知识库**。

**核心研究重心**：多 Agent 的任务编排、tool_call 工具调度、状态协同、冲突消解与结果融合。

---

## 系统架构总览

```
                            ┌──────────────────────────────┐
                            │     Orchestrator 调度协调      │
                            │     Planner 任务规划(DAG)      │
                            │     StateGraph 共享状态总线     │
                            └──────────────┬───────────────┘
                                           │
          ┌────────────────────────────────┼────────────────────────────────┐
          ▼                                ▼                                ▼
   ┌─────────────┐                 ┌─────────────┐                 ┌─────────────┐
   │  Collector  │                 │  Analyzer   │                 │  Reviewer   │
   │ 采集管理Agent│                │ 分析抽取Agent│                │ 质量审核Agent│
   │ tool_call:  │                │ tool_call:  │                │ tool_call:  │
   │ RSS/Web爬虫 │                │ 小模型NER    │                │ Schema校验  │
   │ 正文清洗    │                │ LLM关系抽取  │                │ 冲突检测    │
   │ SimHash去重 │                │ 事件抽取     │                │ LLM仲裁     │
   └─────────────┘                │ 摘要生成     │                │ 置信度过滤   │
                                  └─────────────┘                └─────────────┘
                                           │                                │
                                  ┌────────┴────────┐                       │
                                  ▼                 ▼                       │
                           ┌───────────┐    ┌───────────┐                  │
                           │Knowledge  │    │  MySQL    │                  │
                           │ Modeler   │    │  Neo4j    │                  │
                           │知识建模    │    │ 数据存储   │                  │
                           │实体链接消歧│    │ 图谱查询   │                  │
                           │关系融合去重│    └───────────┘                  │
                           └───────────┘                                   │
                                  │                                        │
                                  └────────────────────────────────────────┘
                                           │
                                           ▼
                                    ┌─────────────┐
                                    │  报告生成    │
                                    │ Markdown    │
                                    │ + JSON      │
                                    └─────────────┘
```

---

## 6 大 Agent 详细说明

### 1. Orchestrator（调度协调Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/orchestrator.py](src/agents/orchestrator.py) |
| **职责** | 接收任务指令，解析用户意图，启动协同流程，汇总各Agent结果，触发报告生成 |
| **注册工具** | `parse_intent`（意图解析）、`launch_workflow`（流程启动）、`aggregate_results`（结果汇总） |
| **输入** | 用户自然语言意图 |
| **输出** | 结构化的任务描述 + 最终分析报告 |
| **不做什么** | 不直接操作数据、不直接调用LLM做内容分析 |

### 2. Planner（任务规划Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/planner.py](src/agents/planner.py) |
| **职责** | 将高层意图分解为可执行的原子任务DAG，定义任务间依赖与并行策略 |
| **注册工具** | `build_task_dag`（DAG构建）、`resolve_dependencies`（依赖解析/拓扑排序）、`optimize_parallelism`（并行度优化） |
| **分解逻辑** | 纵向按数据链路（采集→清洗→分析→建模→质检）切分，横向按数据源并行 |
| **输出** | 包含 10 个节点的任务DAG，附带拓扑排序结果和并行批次规划 |

### 3. Collector（采集管理Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/collector.py](src/agents/collector.py) |
| **职责** | 调度爬虫工具执行多源数据抓取，执行去重与正文清洗 |
| **注册工具** | `fetch_rss`（RSS抓取）、`scrape_web`（动态网页爬虫）、`clean_article`（正文清洗）、`dedup_check`（去重检测） |
| **tool_call决策** | 根据URL特征自动选择工具——RSS链接调用RSS工具，网页URL调用爬虫工具 |
| **支持数据源** | RSS/Atom订阅源、动态网页（httpx+readability）、社交媒体API（Reddit等） |
| **输出** | 标准化原始数据条目 + 清洗后文档列表 |

### 4. Analyzer（分析抽取Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/analyzer.py](src/agents/analyzer.py) |
| **职责** | 对清洗后文本执行分层NLP信息抽取 |
| **注册工具** | `extract_entities`（NER实体识别）、`extract_relations`（关系抽取）、`extract_events`（事件抽取）、`summarize`（摘要生成） |
| **分层调用策略** | ①小模型NER（零API成本）→ ②LLM关系抽取（仅处理含实体的文本）→ ③事件抽取（含时间/地点才触发）→ ④摘要生成 |
| **降级机制** | LLM不可用时：规则NER → 共现关系 → 抽取式摘要 |
| **输出** | 实体列表（PER/ORG/LOC/TIME/EVENT/TOPIC）、关系三元组、事件卡片、文本摘要 |

### 5. Knowledge Modeler（知识建模Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/knowledge_modeler.py](src/agents/knowledge_modeler.py) |
| **职责** | 将分析结果融合到知识库，执行实体链接消歧与关系融合 |
| **注册工具** | `link_entity`（实体链接）、`update_graph`（图谱更新）、`fuse_relations`（关系融合）、`retrieve_context`（子图上下文检索） |
| **消歧策略** | 三级漏斗：向量相似初筛 → 图谱结构匹配 → LLM终判 |
| **输出** | 图谱更新日志、新增/变更的实体与关系清单、冲突报告 |

### 6. Reviewer（质量审核Agent）
| 项目 | 说明 |
|------|------|
| **文件** | [src/agents/reviewer.py](src/agents/reviewer.py) |
| **职责** | 对全链路产出进行质量校验，裁决通过/不通过，触发修正回路 |
| **注册工具** | `validate_schema`（Schema校验）、`detect_conflicts`（冲突检测）、`llm_arbitrate`（LLM仲裁）、`filter_low_confidence`（置信度过滤） |
| **质检维度** | Schema完整性、实体一致性、关系合理性、置信度评估 |
| **修正回路** | 不通过 → 清除低质量结果 → 重新分析建模 → 再审核（最多3次） |

---

## 共享状态（SharedState）

所有 Agent 通过 **SharedState** 实现数据流转——每个 Agent 执行完毕后更新 State，下游 Agent 从 State 读取上游产出。

```
SharedState = {
    task_id, intent, status,        # 任务元信息
    plan: TaskDAG,                  # Planner产出的任务DAG
    raw_documents[],                # Collector→原始数据
    cleaned_documents[],            # Collector→清洗后数据
    extracted_entities[],           # Analyzer→实体列表
    extracted_relations[],          # Analyzer→关系三元组
    extracted_events[],             # Analyzer→事件卡片
    confidence_scores{},            # 各阶段置信度
    review_flags[],                 # Reviewer→质检标记
    report,                         # 最终分析报告(Markdown)
    knowledge_updates{},            # 知识库变更摘要
    execution_log[],                # 完整执行审计日志
}
```

**数据模型**（Pydantic强类型约束）：

| 模型 | 核心字段 |
|------|----------|
| `Document` | id, title, content, url, source, publish_time |
| `Entity` | name, type(PER/ORG/LOC/TIME/EVENT/TOPIC), confidence, source |
| `Relation` | head, tail, relation_type, confidence, evidence |
| `Event` | name, trigger_word, participants[], location, time |
| `TaskNode` | node_id, agent_type, task_type, dependencies[], priority |
| `ReviewFlag` | type, severity(critical/warning/info), target, description, suggestion |

---

## 执行流程（LangGraph编排）

```
START
  │
  ▼
[plan]        Planner：意图 → 任务DAG（10节点/6阶段）
  │
  ▼
[collect]     Collector：多源并行采集 + 清洗 + 去重
  │            （有Demo数据时自动跳过网络采集）
  ▼
[analyze]     Analyzer：分层NLP抽取（NER→关系→事件→摘要）
  │
  ▼
[model]       KnowledgeModeler：实体链接消歧 + 关系融合 + 图谱更新
  │
  ▼
[review]      Reviewer：Schema校验 → 冲突检测 → 置信度过滤 → LLM仲裁
  │
  ├── 通过(置信度≥0.7 且无严重问题) → [report] → END
  │
  └── 不通过 → [correct] → 清除低质量数据 → 重新analyze → re-model → re-review
                   │
                   └── 修正超过3次 → 标记PARTIAL → [report] → END
```

**条件路由**：
- 质检置信度 ≥ 0.7 且无 critical 问题 → 通过，生成报告
- 质检置信度 < 0.7 或有 critical 问题 → 进入修正回路
- 修正次数超过上限(3次) → 标记 PARTIAL，仍产出报告但附带不确定性标注

---

## 项目目录结构

```
media-knowledge-system/
│
├── main.py                          # 主入口，含Demo数据，组装Agent集群并启动
├── requirements.txt                 # Python依赖清单（LangGraph/Neo4j/Scrapy等）
│
├── config/
│   ├── settings.yaml                # 全局配置（LLM/DB/Agent/报告参数）
│   └── prompts/                     # 5类Prompt模板（YAML格式）
│       ├── ner.yaml                 # 命名实体识别Prompt
│       ├── re.yaml                  # 关系抽取Prompt
│       ├── summary.yaml             # 摘要生成Prompt
│       ├── report.yaml              # 报告撰写Prompt
│       └── review.yaml              # 质检审核Prompt
│
├── sql/
│   └── init.sql                     # MySQL完整建表脚本（7张表）
│
├── src/
│   ├── agents/                      # ★ 多智能体核心模块
│   │   ├── state.py                 # SharedState + 数据模型定义
│   │   ├── base.py                  # Agent基类 + Tool工具框架(@tool装饰器)
│   │   ├── orchestrator.py          # 调度协调Agent
│   │   ├── planner.py              # 任务规划Agent（DAG+拓扑排序）
│   │   ├── collector.py            # 采集管理Agent（RSS+Web+去重清洗）
│   │   ├── analyzer.py             # 分析抽取Agent（分层NLP）
│   │   ├── knowledge_modeler.py    # 知识建模Agent（消歧+融合）
│   │   ├── reviewer.py             # 质量审核Agent（校验+仲裁）
│   │   └── graph_orchestrator.py   # LangGraph编排引擎（DAG执行+条件路由）
│   │
│   ├── data/                        # 数据采集与处理模块
│   │   ├── collectors/
│   │   │   ├── base.py             # 采集器抽象基类
│   │   │   ├── rss.py              # RSS采集器（feedparser）
│   │   │   ├── web_scraper.py      # 动态网页爬虫（httpx+readability）
│   │   │   └── social_api.py       # 社交媒体API采集器（Reddit等）
│   │   ├── cleaner.py              # 多阶段清洗流水线（去噪+质量评分）
│   │   └── chunker.py              # 文本分块器（固定/句子/段落三种策略）
│   │
│   ├── nlp/                         # NLP信息抽取模块
│   │   ├── ner.py                  # 命名实体识别（HanLP优先→已知实体匹配→规则降级）
│   │   ├── relation_extract.py     # 关系抽取（LLM+共现规则降级）
│   │   ├── event_extract.py        # 事件抽取（触发词检测+LLM论元标注）
│   │   └── summarizer.py           # 摘要生成（LLM生成式+TextRank抽取式）
│   │
│   ├── knowledge/                   # 知识库操作模块
│   │   ├── graph_store.py          # Neo4j图谱CRUD（MERGE/CREATE/子图检索/热点发现）
│   │   ├── entity_linker.py        # 三级漏斗实体消歧（名称相似→图谱结构→LLM仲裁）
│   │   └── mysql_repo.py           # MySQL数据访问层（7表完整CRUD操作）
│   │
│   ├── llm/                         # LLM调用模块
│   │   ├── llm_client.py           # API客户端（Anthropic/OpenAI+Mock降级+成本追踪）
│   │   └── prompt_manager.py       # Prompt模板管理器（YAML加载+变量插值）
│   │
│   └── report/                      # 报告生成模块
│       ├── generator.py            # 4类报告生成器（Hotspot/Entity/Trend/Quality）
│       └── templates/              # 报告Markdown骨架模板
│           ├── hotspot.md
│           ├── entity_analysis.md
│           ├── topic_trend.md
│           └── data_quality.md
│
├── tests/                           # 单元测试套件
│   ├── conftest.py                 # Pytest共享fixtures（示例文本/实体/State）
│   ├── test_agents/
│   │   ├── test_collector.py       # Collector工具+流程测试
│   │   ├── test_analyzer.py        # Analyzer抽取+降级测试
│   │   └── test_reviewer.py        # Reviewer校验+冲突检测测试
│   ├── test_nlp/
│   │   └── test_ner.py             # NER引擎+类型映射测试
│   └── test_knowledge/
│       └── test_graph_store.py     # 图谱操作+实体链接测试
│
└── notebooks/                       # Jupyter实验笔记本
    ├── 01_data_exploration.ipynb   # 数据清洗+NER探索
    ├── 02_ner_experiment.ipynb     # NER方案对比实验
    └── 03_graph_analysis.ipynb     # 知识图谱分析
```

---

## 运行模式

### Demo模式（当前默认，无需外部依赖）
项目内置 3 篇中文AI新闻样本，无需网络采集和API Key即可跑通全流程。

```bash
cd media-knowledge-system
pip install -r requirements.txt
python main.py
```

输出：
- 控制台打印完整分析报告
- `output/report_task_demo_001.md` — Markdown格式报告

### 生产模式（需配置）
1. 设置环境变量 `ANTHROPIC_API_KEY`（或 `OPENAI_API_KEY`）
2. 安装并启动 MySQL + Neo4j
3. 修改 `config/settings.yaml` 中的数据库连接和RSS源
4. 运行 `python main.py`

---

## 容错与降级策略

系统在多级依赖不可用时自动降级，保证流程不中断：

| 依赖 | 正常模式 | 降级模式 |
|------|----------|----------|
| LLM API | Claude/GPT 高质量抽取 | Mock响应 + 规则抽取 |
| HanLP | 本地高精度NER | 已知实体匹配 + 正则规则 |
| Neo4j | 图谱存储与查询 | 内存模拟（日志记录） |
| MySQL | 持久化存储 | 内存模拟（日志记录） |
| 网络采集 | Scrapy+Playwright | Demo内置数据 |
| RSS源 | feedparser实时抓取 | 返回空列表 |

---

## 技术栈

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| Agent编排 | LangGraph StateGraph | 有状态图原生支持多Agent流水线、条件路由 |
| LLM | Claude API / OpenAI API | 高质量抽取与报告生成 |
| NLP | HanLP / 规则匹配 | 本地推理零API成本，规则降级保证可用性 |
| 爬虫 | httpx + feedparser | 异步高性能，支持RSS+动态网页 |
| 关系数据库 | MySQL + SQLAlchemy | 结构化业务数据、Agent执行日志持久化 |
| 图数据库 | Neo4j | 知识图谱首选，Cypher表达力强 |
| 语言 | Python 3.11+ | LangGraph/Scrapy/HanLP均为Python原生生态 |

---

## 关键技术难点与解决方案

| 难点 | 解决方案 |
|------|----------|
| **Agent状态一致性** | LangGraph StateGraph单一状态源，原子更新 |
| **任务DAG动态调整** | Planner可被重新触发，支持降级计划 |
| **实体对齐准确性** | 三级漏斗：名称相似→图谱结构→LLM仲裁 |
| **LLM成本控制** | 分层策略：小模型粗筛+LLM精抽，Mock降级 |
| **断点恢复** | State定期MySQL持久化（生产模式） |
| **修正回路** | 质检不通过→清除低质量数据→重分析→重审核（最多3次） |
