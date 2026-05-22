"""
共享状态数据结构 —— 多Agent协同的核心数据总线

LangGraph StateGraph 的 State 类型，在各Agent节点间流转。
每个Agent节点读取State中的上游产出，处理后追加写入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class Entity(BaseModel):
    """实体数据模型"""
    name: str
    type: str  # PER / ORG / LOC / TIME / EVENT / TOPIC
    confidence: float
    source: str = ""
    first_seen: Optional[str] = None
    mentions: List[str] = field(default_factory=list)
    neo4j_id: Optional[int] = None


class Relation(BaseModel):
    """关系三元组模型"""
    head: str
    tail: str
    relation_type: str
    confidence: float
    evidence: Optional[str] = None
    source_article_id: Optional[str] = None


class Event(BaseModel):
    """事件模型"""
    name: str
    trigger_word: str = ""
    participants: List[Dict[str, str]] = field(default_factory=list)
    location: Optional[str] = None
    time: Optional[str] = None
    confidence: float = 0.8
    source: str = ""


class Document(BaseModel):
    """文档模型"""
    id: str
    title: str
    content: str
    url: str = ""
    source: str = ""
    source_type: str = "web"
    publish_time: str = ""
    fetch_time: Optional[str] = None
    language: str = "zh"
    checksum: Optional[str] = None


class ReviewFlag(BaseModel):
    """质检标记"""
    type: str  # schema_violation / entity_conflict / relation_error / low_confidence
    severity: str  # critical / warning / info
    target: str
    description: str
    suggestion: str


class TaskNode(BaseModel):
    """任务DAG节点"""
    node_id: str
    agent_type: str
    task_type: str
    params: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    priority: int = 5
    estimated_duration_ms: int = 0


class TaskDAG(BaseModel):
    """任务DAG"""
    nodes: List[TaskNode] = field(default_factory=list)
    edges: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class SharedState:
    """
    多Agent共享状态 —— 编排图的数据总线

    每个Agent节点：
    1. 从State读取上游产出
    2. 执行自身逻辑
    3. 追加写入State
    4. 返回更新后的State
    """

    # --- 任务元信息 ---
    task_id: str = ""
    intent: str = ""
    status: TaskStatus = TaskStatus.PENDING
    plan: Optional[TaskDAG] = None
    current_stage: str = "init"

    # --- 数据流 ---
    raw_documents: List[Document] = field(default_factory=list)
    cleaned_documents: List[Document] = field(default_factory=list)
    extracted_entities: List[Entity] = field(default_factory=list)
    extracted_relations: List[Relation] = field(default_factory=list)
    extracted_events: List[Event] = field(default_factory=list)

    # --- 质量元数据 ---
    confidence_scores: Dict[str, float] = field(default_factory=dict)
    review_flags: List[ReviewFlag] = field(default_factory=list)
    correction_count: int = 0
    max_corrections: int = 3

    # --- 最终产出 ---
    report: Optional[str] = None
    report_json: Optional[Dict[str, Any]] = None
    knowledge_updates: Dict[str, Any] = field(default_factory=dict)

    # --- 持久化 ---
    checkpoint_id: Optional[str] = None
    execution_log: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（供LangGraph StateGraph使用）"""
        return {
            "task_id": self.task_id,
            "intent": self.intent,
            "status": self.status.value,
            "plan": self.plan.model_dump() if self.plan else None,
            "current_stage": self.current_stage,
            "raw_documents": [d.model_dump() for d in self.raw_documents],
            "cleaned_documents": [d.model_dump() for d in self.cleaned_documents],
            "extracted_entities": [e.model_dump() for e in self.extracted_entities],
            "extracted_relations": [r.model_dump() for r in self.extracted_relations],
            "extracted_events": [ev.model_dump() for ev in self.extracted_events],
            "confidence_scores": self.confidence_scores,
            "review_flags": [f.model_dump() for f in self.review_flags],
            "correction_count": self.correction_count,
            "max_corrections": self.max_corrections,
            "report": self.report,
            "report_json": self.report_json,
            "knowledge_updates": self.knowledge_updates,
            "checkpoint_id": self.checkpoint_id,
            "execution_log": self.execution_log,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedState":
        """从字典反序列化"""
        state = cls(
            task_id=data.get("task_id", ""),
            intent=data.get("intent", ""),
            status=TaskStatus(data.get("status", "PENDING")),
            current_stage=data.get("current_stage", "init"),
            confidence_scores=data.get("confidence_scores", {}),
            correction_count=data.get("correction_count", 0),
            max_corrections=data.get("max_corrections", 3),
            report=data.get("report"),
            report_json=data.get("report_json"),
            knowledge_updates=data.get("knowledge_updates", {}),
            checkpoint_id=data.get("checkpoint_id"),
            execution_log=data.get("execution_log", []),
        )

        if data.get("plan"):
            state.plan = TaskDAG(**data["plan"])

        state.raw_documents = [Document(**d) for d in data.get("raw_documents", [])]
        state.cleaned_documents = [Document(**d) for d in data.get("cleaned_documents", [])]
        state.extracted_entities = [Entity(**e) for e in data.get("extracted_entities", [])]
        state.extracted_relations = [Relation(**r) for r in data.get("extracted_relations", [])]
        state.extracted_events = [Event(**ev) for ev in data.get("extracted_events", [])]
        state.review_flags = [ReviewFlag(**f) for f in data.get("review_flags", [])]

        return state
