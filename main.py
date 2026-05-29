import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.agents.graph_orchestrator import GraphOrchestrator
from src.agents.planner import PlannerAgent
from src.agents.collector import CollectorAgent
from src.agents.analyzer import AnalyzerAgent
from src.agents.knowledge_modeler import KnowledgeModelerAgent
from src.agents.reviewer import ReviewerAgent
from src.llm.llm_client import LLMClient
from src.llm.prompt_manager import PromptManager
from src.knowledge.mysql_repo import MySQLRepository
from src.knowledge.graph_store import GraphStore
from src.data.cleaner import DataCleaner
from src.nlp.ner import NERExtractor
from demo import DEMO_DOCUMENTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("main")



async def build_agents(llm_client: LLMClient, config: dict):
    """构建所有 Agent 实例"""
    # 读取 MySQL 配置
    mysql_cfg = config.get("mysql", {})
    mysql_password = mysql_cfg.get("password", "")
    # 去掉 ${} 包装（环境变量语法）
    if mysql_password.startswith("${") and mysql_password.endswith("}"):
        env_var = mysql_password[2:-1]
        import os
        mysql_password = os.environ.get(env_var, "")

    mysql_repo = MySQLRepository(
        host=mysql_cfg.get("host", "localhost"),
        port=mysql_cfg.get("port", 3306),
        user=mysql_cfg.get("user", "root"),
        password=mysql_password,
        database=mysql_cfg.get("database", "media_knowledge_db"),
        charset=mysql_cfg.get("charset", "utf8mb4"),
    )
    # 读取 Neo4j 配置
    neo4j_cfg = config.get("neo4j", {})
    neo4j_password = neo4j_cfg.get("password", "password")
    if neo4j_password.startswith("${") and neo4j_password.endswith("}"):
        import os
        neo4j_password = os.environ.get(neo4j_password[2:-1], "password")

    graph_store = GraphStore(
        uri=neo4j_cfg.get("uri", "http://localhost:7687"),
        user=neo4j_cfg.get("user", "neo4j"),
        password=neo4j_password,
        database=neo4j_cfg.get("database", "neo4j"),
    )
    cleaner = DataCleaner()

    agents = {
        "planner": PlannerAgent(llm_client),
        "collector": CollectorAgent(llm_client, cleaner, mysql_repo),
        "analyzer": AnalyzerAgent(llm_client, small_model_ner=NERExtractor(engine="rule")),
        "knowledge_modeler": KnowledgeModelerAgent(llm_client, mysql_repo, graph_store),
        "reviewer": ReviewerAgent(llm_client, mysql_repo),
    }
    return agents


async def main():
    """主函数 —— 批量管线模式"""
    logger.info("=" * 60)
    logger.info("多智能体协同媒体数据分析与知识库构建系统 启动中...")
    logger.info("=" * 60)

    config = load_config()
    run_mode = config.get("mode", "demo")
    logger.info(f"运行模式: {run_mode}")

    llm_client = build_llm_client(config)
    logger.info(f"LLM: provider={llm_client.provider}, model={llm_client.model}, "
                f"key={'已设置' if llm_client.api_key else '未设置'}")

    # 构建 Agent 集群
    agents = await build_agents(llm_client, config)
    logger.info(f"已初始化 {len(agents)} 个 Agent: {list(agents.keys())}")

    # 创建编排图
    orchestrator = GraphOrchestrator(agents)

    # 读取数据源配置
    collector_cfg = config.get("agents", {}).get("collector", {})
    sources_config = collector_cfg.get("sources", {})

    # 示例任务
    task_id = "task_demo_001" if run_mode == "demo" else "task_prod_001"
    intent = "分析本周AI行业热点事件"

    logger.info(f"任务ID: {task_id}")
    logger.info(f"意图: {intent}")

    # Demo模式使用内置数据，生产模式使用网络采集
    demo_docs = DEMO_DOCUMENTS if run_mode == "demo" else None

    try:
        result = await orchestrator.run(
            task_id=task_id,
            intent=intent,
            demo_documents=demo_docs,
            sources_config=sources_config,
        )
        logger.info("=" * 60)
        logger.info("任务执行完成")
        logger.info(f"状态: {result.status.value}")
        logger.info(f"采集文章: {len(result.raw_documents)} 篇")
        logger.info(f"清洗文章: {len(result.cleaned_documents)} 篇")
        logger.info(f"提取实体: {len(result.extracted_entities)} 个")
        logger.info(f"提取关系: {len(result.extracted_relations)} 条")
        logger.info(f"质检标记: {len(result.review_flags)} 条")

        if result.report:
            # 保存报告（带时间戳，每次不覆盖）
            from datetime import datetime as _dt
            report_dir = Path("output")
            report_dir.mkdir(exist_ok=True)
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            report_path = report_dir / f"report_{task_id}_{timestamp}.md"
            report_path.write_text(result.report, encoding="utf-8")
            logger.info(f"报告已保存至: {report_path}")

            # # 同时打印到控制台
            # print("\n" + "=" * 60)
            # print(result.report)
            # print("=" * 60)

        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"任务执行失败: {e}", exc_info=True)
        sys.exit(1)


def load_config() -> dict:
    """加载配置文件"""
    import yaml
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_llm_client(config: dict) -> LLMClient:
    """从配置构建 LLM 客户端"""
    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    if api_key.startswith("${") and api_key.endswith("}"):
        api_key = api_key[2:-1]

    return LLMClient(
        provider=llm_cfg.get("provider", "anthropic"),
        model=llm_cfg.get("model", "claude-sonnet-4-6"),
        api_key=api_key,
        base_url=llm_cfg.get("base_url", ""),
        max_tokens=llm_cfg.get("max_tokens", 4096),
        temperature=llm_cfg.get("temperature", 0.1),
        request_timeout=llm_cfg.get("request_timeout", 60),
    )


def build_graph_store(config: dict) -> GraphStore:
    """从配置构建图谱存储"""
    neo4j_cfg = config.get("neo4j", {})
    neo4j_password = neo4j_cfg.get("password", "password")
    if neo4j_password.startswith("${") and neo4j_password.endswith("}"):
        import os
        neo4j_password = os.environ.get(neo4j_password[2:-1], "password")

    return GraphStore(
        uri=neo4j_cfg.get("uri", "http://localhost:7687"),
        user=neo4j_cfg.get("user", "neo4j"),
        password=neo4j_password,
        database=neo4j_cfg.get("database", "neo4j"),
    )


def chat_mode(port: int = 7860, share: bool = False):
    """启动对话交互界面"""
    from src.agents.dialogue_manager import DialogueManager
    from src.ui.app import launch_app

    logger.info("=" * 60)
    logger.info("启动对话交互模式")
    logger.info("=" * 60)

    config = load_config()
    llm_client = build_llm_client(config)
    logger.info(f"LLM: provider={llm_client.provider}, model={llm_client.model}, "
                f"key={'已设置' if llm_client.api_key else '未设置（Mock模式）'}")

    # 对话模式使用纯内存图谱，跳过 Neo4j 连接避免超时等待
    graph_store = GraphStore(memory_only=True)
    prompt_manager = PromptManager()

    dialogue_manager = DialogueManager(
        llm_client=llm_client,
        graph_store=graph_store,
        prompt_manager=prompt_manager,
    )

    launch_app(dialogue_manager, port=port, share=share)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多智能体协同媒体数据分析与知识库构建系统")
    parser.add_argument("--chat", action="store_true", help="启动对话交互界面")
    parser.add_argument("--port", type=int, default=7860, help="对话界面端口 (默认7860)")
    parser.add_argument("--share", action="store_true", help="创建 Gradio 公网链接")
    args = parser.parse_args()

    if args.chat:
        chat_mode(port=args.port, share=args.share)
    else:
        asyncio.run(main())
