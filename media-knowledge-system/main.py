"""
多智能体协同的媒体数据分析与知识库构建系统 —— 主入口
"""
import asyncio
import logging
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.agents.graph_orchestrator import GraphOrchestrator
from src.agents.orchestrator import OrchestratorAgent
from src.agents.planner import PlannerAgent
from src.agents.collector import CollectorAgent
from src.agents.analyzer import AnalyzerAgent
from src.agents.knowledge_modeler import KnowledgeModelerAgent
from src.agents.reviewer import ReviewerAgent
from src.agents.state import Document
from src.llm.llm_client import LLMClient
from src.llm.prompt_manager import PromptManager
from src.knowledge.mysql_repo import MySQLRepository
from src.knowledge.graph_store import GraphStore
from src.data.cleaner import DataCleaner
from src.nlp.ner import NERExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("main")

# ============================================================
# 内置演示数据 —— 无需外部采集即可跑通全流程
# ============================================================
DEMO_DOCUMENTS = [
    Document(
        id="demo_001",
        title="OpenAI发布GPT-5，多模态能力大幅提升",
        content="""
        2026年5月20日，OpenAI在美国旧金山正式发布了新一代大语言模型GPT-5。
        OpenAI CEO Sam Altman在发布会上表示，GPT-5在推理能力、多模态理解和代码生成方面
        取得了重大突破。该模型支持文本、图像、音频和视频的联合理解与生成，
        在MMLU基准测试中达到95.8%的准确率。微软公司作为OpenAI的最大投资方，
        宣布将在Azure云平台率先部署GPT-5。Google DeepMind首席执行官Demis Hassabis
        对此表示祝贺，同时透露Google正在开发下一代Gemini模型以保持竞争力。
        分析人士指出，GPT-5的发布将推动全球AI产业进入新阶段。
        """,
        url="https://techcrunch.com/gpt5-launch",
        source="TechCrunch",
        publish_time="2026-05-20T10:00:00",
    ),
    Document(
        id="demo_002",
        title="阿里巴巴投资50亿建设上海AI研究院",
        content="""
        2026年5月18日，阿里巴巴集团宣布与上海市政府达成战略合作协议，
        将在浦东新区投资50亿元人民币建设人工智能研究院。阿里巴巴CEO吴泳铭在上海
        出席了签约仪式并讲话。该研究院将聚焦大语言模型、自动驾驶和量子计算三大方向，
        计划在三年内招募1000名研究员。上海市市长表示，这一合作将有力推动
        长三角地区的数字经济发展。此前，腾讯公司也在深圳成立了AI实验室，
        百度则在北京扩建了深度学习研究院。国内AI人才争夺战正在加剧。
        """,
        url="https://reuters.com/alibaba-shanghai-ai",
        source="Reuters",
        publish_time="2026-05-18T08:30:00",
    ),
    Document(
        id="demo_003",
        title="字节跳动推出豆包2.0，中国AI助手市场竞争白热化",
        content="""
        2026年5月16日，字节跳动在深圳举行了年度产品发布会。
        CEO梁汝波宣布推出全新的AI助手产品"豆包2.0"，该产品基于字节跳动自研的
        大语言模型，支持多模态交互和实时联网搜索。梁汝波表示，豆包2.0的日活跃用户
        已突破5000万。与此同时，腾讯公司的混元大模型已在微信中全面接入，
        百度的文心一言4.0也于上周正式上线。昆仑万维、商汤科技等公司
        也纷纷发布了各自的AI助手产品。分析人士认为，中国AI市场正在进入白热化竞争阶段，
        预计2026年市场规模将突破2000亿元。
        """,
        url="https://techcrunch.com/bytedance-doubao2",
        source="TechCrunch",
        publish_time="2026-05-16T14:00:00",
    ),
]


async def build_agents(llm_client: LLMClient, prompt_manager: PromptManager):
    """构建所有 Agent 实例"""
    mysql_repo = MySQLRepository()
    graph_store = GraphStore()
    cleaner = DataCleaner()

    agents = {
        "orchestrator": OrchestratorAgent(llm_client, prompt_manager),
        "planner": PlannerAgent(llm_client),
        "collector": CollectorAgent(llm_client, cleaner),
        "analyzer": AnalyzerAgent(llm_client, small_model_ner=NERExtractor(engine="rule")),
        "knowledge_modeler": KnowledgeModelerAgent(llm_client, mysql_repo, graph_store),
        "reviewer": ReviewerAgent(llm_client, mysql_repo),
    }
    return agents


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("多智能体协同媒体数据分析与知识库构建系统 启动中...")
    logger.info("=" * 60)

    # 读取配置文件
    import yaml
    config_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 读取运行模式
    run_mode = config.get("mode", "demo")
    logger.info(f"运行模式: {run_mode}")

    llm_cfg = config.get("llm", {})
    api_key = llm_cfg.get("api_key", "")
    # 去掉 ${} 包装（如果用户误用了环境变量语法）
    if api_key.startswith("${") and api_key.endswith("}"):
        api_key = api_key[2:-1]

    # 初始化 LLM 客户端（从配置文件读取参数）
    llm_client = LLMClient(
        provider=llm_cfg.get("provider", "anthropic"),
        model=llm_cfg.get("model", "claude-sonnet-4-6"),
        api_key=api_key,
        base_url=llm_cfg.get("base_url", ""),
        max_tokens=llm_cfg.get("max_tokens", 4096),
        temperature=llm_cfg.get("temperature", 0.1),
        request_timeout=llm_cfg.get("request_timeout", 60),
    )
    logger.info(f"LLM: provider={llm_client.provider}, model={llm_client.model}, "
                f"key={'已设置' if llm_client.api_key else '未设置'}")

    prompt_manager = PromptManager()

    # 构建 Agent 集群
    agents = await build_agents(llm_client, prompt_manager)
    logger.info(f"已初始化 {len(agents)} 个 Agent: {list(agents.keys())}")

    # 创建编排图
    orchestrator = GraphOrchestrator(agents)

    # 读取数据源配置
    collector_cfg = config.get("agents", {}).get("collector", {})
    sources_config = collector_cfg.get("sources", {})

    # 示例任务
    task_id = "task_demo_001" if run_mode == "demo" else "task_prod_001"
    intent = "分析本周AI行业热点事件，采集TechCrunch和Reuters科技板块新闻"

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
            # 保存报告
            report_dir = Path("output")
            report_dir.mkdir(exist_ok=True)
            report_path = report_dir / f"report_{task_id}.md"
            report_path.write_text(result.report, encoding="utf-8")
            logger.info(f"报告已保存至: {report_path}")

            # 同时打印到控制台
            print("\n" + "=" * 60)
            print(result.report)
            print("=" * 60)

        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"任务执行失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
