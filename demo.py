"""
内置演示数据 —— 无需外部采集即可跑通全流程

使用方式:
    from demo import DEMO_DOCUMENTS
    result = await orchestrator.run(
        task_id="task_demo_001",
        intent="分析本周AI行业热点事件",
        demo_documents=DEMO_DOCUMENTS,
    )
"""
from src.agents.state import Document

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
