# main.py
import os
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from sentence_transformers import CrossEncoder
# from datetime import datetime
from topic_classifier import TopicClassifier
from database import VectorDatabase

MODEL_PATH = "/root/autodl-fs/Qwen3-8b"

class TopicAwareRAG:
    def __init__(self):
        self.classifier = TopicClassifier(MODEL_PATH)
        self.vector_db = VectorDatabase()
        self.reranker = CrossEncoder('/root/autodl-tmp/paraphrase-multilingual-MiniLM-L12-v2')
        self.current_conversation = []
        self.current_topic = None
        self.current_confidence = None
        self.gen_tokenizer = self.classifier.tokenizer
        self.gen_model = self.classifier.model

    def start_new_conversation(self, initial_input: str):
        """开始新对话，自动识别主题"""
        result = self.classifier.predict_topic(initial_input)
        self.current_topic = result["topic"]
        self.current_confidence = result["confidence"]
        self.current_conversation = []
        print(f"新对话开始 | 主题: {self.current_topic} (置信度: {self.current_confidence})")

    def add_message(self, role: str, content: str):
        self.current_conversation.append({
            "role": role,
            "content": content,
            # "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

    def retrieve_and_rerank(self, query: str, top_k: int = 3):
        retrieved = self.vector_db.retrieve_similar_dialogs(query, self.current_topic, top_k)
        if not retrieved:
            return []
        pairs = [[query, item["user_text"]] for item in retrieved]
        scores = self.reranker.predict(pairs)
        for item, score in zip(retrieved, scores):
            item["reranked_score"] = float(score)
        return sorted(retrieved, key=lambda x: x["reranked_score"], reverse=True)

    def build_prompt(self, query: str, reranked: list, threshold: float = 0.7):
        context = ""
        for item in reranked:
            if item["reranked_score"] < threshold:
                continue
            therapist_responses = [msg["content"] for msg in item["full_messages"] if msg["role"] == "assistant"]
            if therapist_responses:
                context += f"【参考】来访者: {item['user_text'][:100]}...\n咨询师: {therapist_responses[0]}\n\n"

        history = "\n".join([f"{msg['role']}: {msg['content']}" for msg in self.current_conversation[-3:]])

        if not context and not history:
            return f"现在你扮演一位专业的心理咨询师，你具备丰富的心理学和心理健康知识。你擅长运用多种心理咨询技巧，例如认知行为疗法原则、动机访谈技巧和解决问题导向的短期疗法。以温暖亲切的语气，展现出共情和对来访者感受的深刻理解。以自然的方式与来访者进行对话，避免过长或过短的回应，确保回应流畅且类似人类的对话。提供深层次的指导和洞察，使用具体的心理概念和例子帮助来访者更深入地探索思想和感受。避免教导式的回应，更注重共情和尊重来访者的感受。根据来访者的反馈调整回应，确保回应贴合来访者的情境和需求。请为以下的对话生成一个回复。：\n\n{query}"

        return f""" 你是一位温暖、耐心的心理咨询师。请严格遵守以下规则：
                            1. 在第一轮回复中，绝对不要提供建议、技巧、练习或心理学术语。
                            2. 必须先准确识别并回应用户的情绪关键词（如“失眠”“情绪不稳定”“疲惫”）。
                            3. 使用自然、口语化的共情句式，例如：
                               - “听起来你最近真的挺不容易的。”
                               - “那种睡不着觉、情绪起伏大的感觉，一定让你很煎熬吧？”
                               - “我能感受到你现在的无助和迷茫。”
                            4. 用一个温和的开放式问题结尾，邀请用户继续分享，例如：
                               - “你愿意多说说那段时间发生了什么吗？”
                               - “在那些睡不着的夜里，你心里最常想的是什么？”
                现在，请根据以下参考对话和用户输入，生成你的回复：
                以下是相似案例的上下文：
                <context>
                {{context}}
                </context>
                这是之前的对话历史：
                <history>
                {{history}}
                </history>
                现在，来访者提出了以下问题：
                <query>
                {{query}}
                </query>
               
                请在<回答>标签内写下你的回复，不要包含任何其他内容。
                <回答>
                [在此提供你的回复]
                </回答>
                """

    def generate_response(self, prompt: str) -> str:
        inputs = self.gen_tokenizer(prompt, return_tensors="pt").to(self.gen_model.device)
        outputs = self.gen_model.generate(
            **inputs,
            max_new_tokens=512,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1
        )
        response = self.gen_tokenizer.decode(outputs[0], skip_special_tokens=True)
        return response[len(prompt):].strip()

    def consider_topic_shift(self, user_input: str):
        """考虑是否需要切换主题"""
        result = self.classifier.predict_topic(user_input)
        new_topic = result["topic"]
        confidence = result["confidence"]
        
        # 🔥 动态主题切换逻辑
        should_switch = (
            new_topic != self.current_topic and  # 新主题不同
            confidence > 0.85 and  # 置信度足够高
            len(self.current_conversation) > 4  # 至少聊了4轮再切换
        )
        
        if should_switch:
            old_topic = self.current_topic
            self.current_topic = new_topic
            self.current_confidence = confidence
            print(f"🔄 主题切换: {old_topic} → {new_topic} (置信度: {confidence})")
            return True
        return False
    
    
    def process(self, user_input: str):
        print(f"\n用户: {user_input}")
        self.add_message("user", user_input)

        if not self.current_topic:
            self.start_new_conversation(user_input)
        else:
            # 🔥 每3轮检查一次主题是否需要切换
            if len(self.current_conversation) % 3 == 0:
                self.consider_topic_shift(user_input)

        reranked = self.retrieve_and_rerank(user_input)
        print(f"检索到 {len(reranked)} 条参考，最高重排序得分: {reranked[0]['reranked_score']:.3f}" if reranked else "🔍 无参考对话")

        prompt = self.build_prompt(user_input, reranked)
        response = self.generate_response(prompt)
        self.add_message("assistant", response)

        print(f"\n咨询师: {response}")
        return response   

def main():
    rag = TopicAwareRAG()

    # 构建向量库（首次运行需先执行 classify_json 生成 labeled 文件）
    labeled_path = "/root/autodl-tmp/PsyDial-D4_local_labeled.json"
    if os.path.exists(labeled_path):
        rag.vector_db.build_vector_database(labeled_path)
    else:
        print(f"数据文件不存在: {labeled_path}")
        return

    # print("\n" + "="*60)
    # print("心理咨询对话系统已启动")
    # print("输入 'quit' 退出，'summary' 查看摘要")
    # print("="*60)

    while True:
        try:
            user_input = input("\n💬 请输入: ").strip()
            if user_input.lower() == 'quit':
                break
            elif user_input.lower() == 'summary':
                print(f"当前主题: {rag.current_topic}")
                print(f"历史消息数: {len(rag.current_conversation)}")
                continue
            elif not user_input:
                continue
            rag.process(user_input)
        except KeyboardInterrupt:
            break

    print("\n再见！")

if __name__ == "__main__":
    main()