"""
Gradio 对话交互界面

提供：
- 多轮对话聊天窗口
- 知识问答 / 自由对话 模式切换
- 答案溯源展示
- 对话历史管理
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)


def create_app(dialogue_manager: Any) -> Any:
    """
    创建 Gradio 对话应用

    Args:
        dialogue_manager: DialogueManager 实例

    Returns:
        gradio.Blocks 应用实例
    """
    try:
        import gradio as gr
    except ImportError:
        raise ImportError(
            "gradio 未安装，请运行: pip install gradio>=4.0.0"
        )

    # ---- 事件处理函数 ----

    async def respond(
        user_message: str,
        chat_history: List[List[str]],
        mode: str,
    ) -> Tuple[str, List[List[str]], str]:
        """
        处理用户发送的消息

        Returns:
            (清空输入框, 更新后的对话历史, 知识来源文本)
        """
        if not user_message.strip():
            return "", chat_history, ""

        if mode == "知识问答":
            result = await dialogue_manager.chat(user_message)
            answer = result["answer"]
            sources = result["sources"]
        else:
            # 自由对话：直接调用 LLM，不检索知识
            dialogue_manager.history.append({"role": "user", "content": user_message})
            answer = dialogue_manager.llm.chat(dialogue_manager.history)
            dialogue_manager.history.append({"role": "assistant", "content": answer})
            sources = []

        # 更新对话历史
        chat_history = chat_history + [[user_message, answer]]

        # 格式化知识来源
        source_text = _format_sources(sources)

        return "", chat_history, source_text

    def clear_chat() -> Tuple[List, str]:
        """清空对话"""
        dialogue_manager.reset()
        return [], ""

    # ---- 构建界面 ----

    with gr.Blocks(
        title="媒体知识问答系统",
        theme=gr.themes.Soft(),
        css="""
        .source-box {
            max-height: 400px;
            overflow-y: auto;
            font-size: 13px;
        }
        """,
    ) as app:
        gr.Markdown(
            "# 🤖 媒体领域知识问答系统\n"
            "基于多智能体协同构建的媒体知识图谱，支持多轮对话与知识溯源。"
        )

        with gr.Row():
            # 左侧：对话区
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=480,
                    show_label=True,
                    avatar_images=(None, "🤖"),
                )

                with gr.Row():
                    msg_input = gr.Textbox(
                        label="输入问题",
                        placeholder="请输入您的问题，例如：最近AI行业有什么新闻？",
                        lines=1,
                        scale=4,
                        show_label=False,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)

                with gr.Row():
                    mode_select = gr.Radio(
                        choices=["知识问答", "自由对话"],
                        value="知识问答",
                        label="对话模式",
                        scale=3,
                    )
                    clear_btn = gr.Button("🗑️ 清空对话", variant="secondary", scale=1)

            # 右侧：知识来源
            with gr.Column(scale=1):
                gr.Markdown("### 📚 知识来源")
                sources_box = gr.Textbox(
                    label="最近回答引用的知识",
                    lines=20,
                    show_label=False,
                    interactive=False,
                    elem_classes=["source-box"],
                    placeholder="（当知识图谱中有相关信息时，将在此显示引用的实体和关系）",
                )

        # ---- 绑定事件 ----

        # 发送消息（点击按钮 或 按回车）
        msg_input.submit(
            fn=respond,
            inputs=[msg_input, chatbot, mode_select],
            outputs=[msg_input, chatbot, sources_box],
        )
        send_btn.click(
            fn=respond,
            inputs=[msg_input, chatbot, mode_select],
            outputs=[msg_input, chatbot, sources_box],
        )

        # 清空对话
        clear_btn.click(
            fn=clear_chat,
            outputs=[chatbot, sources_box],
        )

    return app


def _format_sources(sources: list) -> str:
    """将知识来源格式化为可读文本"""
    if not sources:
        return ""

    lines = []
    entity_count = 0
    relation_count = 0

    for src in sources:
        if src.get("type") == "entity":
            entity_count += 1
            lines.append(
                f"📌 实体：{src['name']}\n"
                f"   类型：{src.get('entity_type', '未知')} | "
                f"提及：{src.get('mention_count', 0)}次 | "
                f"置信度：{src.get('confidence', 0):.2f}"
            )
        elif src.get("type") == "relation":
            relation_count += 1
            lines.append(
                f"🔗 关系：{src.get('relation_type', '未知')}\n"
                f"   置信度：{src.get('confidence', 0):.2f}"
            )

    if not lines:
        return ""

    summary = f"共引用 {entity_count} 个实体，{relation_count} 条关系\n{'─' * 30}\n"
    return summary + "\n\n".join(lines)


def launch_app(
    dialogue_manager: Any,
    host: str = "0.0.0.0",
    port: int = 7860,
    share: bool = False,
):
    """
    启动对话界面

    Args:
        dialogue_manager: DialogueManager 实例
        host: 监听地址
        port: 监听端口
        share: 是否创建公网链接
    """
    app = create_app(dialogue_manager)
    logger.info(f"启动对话界面: http://localhost:{port}")
    app.launch(server_name=host, server_port=port, share=share)
