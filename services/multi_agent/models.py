"""
多Agent协作模块 - 数据模型
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlanStep:
    """RePlan 模式下的计划步骤"""
    id: str                           # 步骤ID: "step_0", "step_1"
    description: str                  # 步骤描述
    agent_name: str = ""              # 路由到的 Agent 名称
    agent_display: str = ""           # Agent 显示名
    status: str = "pending"           # pending | running | done | error | skipped
    result: str = ""                  # 执行结果摘要
    depends_on: list[str] = field(default_factory=list)  # 依赖的步骤ID


@dataclass
class Plan:
    """RePlan 模式下的执行计划"""
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 0                  # 计划版本（每次重规划 +1）
    is_complete: bool = False
    tech_stack: str = ""              # 推荐的技术栈（如 Python+PyQt5）


@dataclass
class SubAgentDef:
    """子Agent定义"""
    name: str                        # 唯一标识: "code_agent"
    display_name: str                # 显示名称: "代码工程师"
    role_prompt: str                 # 系统提示词
    tool_names: list[str]            # 关联的工具名列表（空=全部）
    max_steps: int = 6               # 独立最大步数
    trigger_keywords: list[str] = field(default_factory=list)  # 路由触发词
    llm_model: str = "inherit"       # 模型: "inherit" | 具体模型名
    temperature: float = 0.3         # LLM 温度

    @classmethod
    def from_dict(cls, data: dict) -> "SubAgentDef":
        return cls(
            name=data["name"],
            display_name=data["display_name"],
            role_prompt=data.get("role_prompt", ""),
            tool_names=data.get("tool_names", []),
            max_steps=data.get("max_steps", 6),
            trigger_keywords=data.get("trigger_keywords", []),
            llm_model=data.get("llm_model", "inherit"),
            temperature=data.get("temperature", 0.3),
        )


@dataclass
class SubTask:
    """编排器分解出的子任务"""
    id: str                          # 子任务ID
    description: str                 # 任务描述（给子Agent的自然语言指令）
    agent_name: str                  # 路由到的Agent名称
    depends_on: list[str] = field(default_factory=list)  # 依赖的子任务ID


@dataclass
class SubTaskResult:
    """子任务执行结果"""
    task_id: str
    agent_name: str
    agent_display: str               # 显示名称
    success: bool
    output: str                      # 最终输出
    steps: list = field(default_factory=list)  # 内部 ReAct 步骤
    error: str = ""                  # 失败时的错误信息
    duration_ms: float = 0           # 执行耗时(ms)


@dataclass
class OrchestrationResult:
    """编排器最终输出"""
    success: bool
    final_answer: str                # 合并总结后的最终回答
    sub_results: list[SubTaskResult] = field(default_factory=list)
    agent_count: int = 0             # 参与的Agent数量
    total_duration_ms: float = 0     # 总耗时(ms)
    routing_reason: str = ""         # 路由决策原因
