# ReAct Agent 与 Plan-and-Execute Agent 架构对比分析

> 基于 `D:/qt_project/LLM_Agent` 项目实际代码实现的技术分析

---

## 一、项目中的两种Agent实现

### 1. ReAct Agent — `services/core/agent_service.py`

**核心类**: `AgentService`（约1500行）

**实现位置**: `_run_iter()` 方法

**核心循环**:
```
用户输入 → Think(LLM生成工具调用) → Act(本地执行工具) → Observe(结果追加到消息历史) → 再次Think → ...
```

**关键设计**:
- **双模式兼容**: API模型使用Function Calling，浏览器模型（MiniMax等）使用文本JSON
- **意图兜底**: 当模型未输出工具JSON时，`_build_direct_tool_calls()`根据用户意图直接构造工具调用
- **反幻觉机制**: 检测模型声称写入文件但未实际调用write_file的情况
- **流式输出**: 支持浏览器模型的实时流式响应
- **工具过滤**: `_filter_tool_input()`过滤LLM幻觉产生的无效参数

**适用场景**: 开放式问题、路径不确定的任务（如研究、调试、探索）

---

### 2. Plan-and-Execute Agent — `services/multi_agent/orchestrator.py`

**核心类**: `OrchestratorAgent`（约62KB）

**执行流程**:
```
1. LLM分析复杂度 → 2. 生成结构化计划 → 3. 逐步执行 → 4. 必要时RePlan → 5. 汇总结果
```

**关键设计**:
- **复杂度分析**: `_quick_ack_and_classify()`一次LLM调用完成复杂度判断+计划生成
- **RePlan机制**: 步骤未完成时自动插入continue_step重试
- **多Agent协作**: 支持关键词匹配/LLM路由选择子Agent
- **上下文传递**: `_build_step_context()`注入前序步骤的技术栈、已创建文件等信息
- **完成验证**: `_check_agent_completion()`检测Agent是否真正完成任务

**适用场景**: 步骤明确的多步任务、需要多Agent协作的复杂项目

---

## 二、两种架构的核心区别

| 维度 | ReAct Agent | Plan-and-Execute Agent |
|------|-------------|------------------------|
| **思考时机** | 每步都思考（Think-Act-Observe循环） | 开头思考一次，后续按计划执行 |
| **计划性** | 无预先计划，动态决策 | 先生成完整计划，再逐步执行 |
| **可预测性** | 较低，可能无限循环 | 较高，步骤和成本可控 |
| **工具调用** | 每步都可能调用工具 | 按计划分配工具调用 |
| **重规划** | 无（每步重新决策） | 支持RePlan，可调整后续步骤 |
| **多Agent** | 单Agent | 支持多Agent协作 |
| **适用场景** | 开放式探索、调试、研究 | 多步任务、项目开发、复杂协作 |

---

## 三、详细实现对比

### 3.1 初始化与配置

**ReAct Agent**:
```python
class AgentService:
    def __init__(self):
        self._tools = get_all_tools()  # 加载所有工具
        self._tool_map = {t.name: t for t in self._tools}
        self._config = get_agent_config()  # max_steps=15, temperature=0.3
```

**Plan-and-Execute Agent**:
```python
class OrchestratorAgent:
    def __init__(self, agent_service, config: dict = None):
        self._agent_service = agent_service  # 复用ReAct Agent
        self._registry = get_registry()  # 子Agent注册中心
        self._config = config or {}  # parallel_execution, route_strategy等
```

### 3.2 核心执行循环

**ReAct Agent** (`_run_iter`):
```python
for _ in range(max_steps):
    # 1. 调用LLM
    response = chat_completion(messages=messages, ...)
    
    # 2. 解析工具调用
    tool_calls = _parse_tool_calls(response, user_message)
    
    # 3. 执行工具
    for tc in tool_calls:
        result = self._invoke_tool_call(tc['tool'], tc['input'])
        tool_results.append(result)
    
    # 4. 将结果追加到消息，继续循环
    messages.append({"role": "user", "content": _build_tool_followup_user_message(tool_results)})
```

**Plan-and-Execute Agent** (`_run_replan_with_plan`):
```python
# 1. 生成计划
plan = self._generate_plan(user_message, agent_info, ...)

# 2. 逐步执行
for step in plan.steps:
    agent_def = self._registry.get(step.agent_name)
    executor = SubAgentExecutor(agent_def, self._agent_service, self._tool_map)
    
    for event in executor.execute(task=step.description, ...):
        yield event  # 透传子Agent事件

# 3. 必要时重规划
if not is_complete:
    continue_step = PlanStep(description="继续完成上一任务...")
    plan.steps = plan.steps[:step_idx+1] + [continue_step] + remaining_steps

# 4. 汇总结果
final_answer = self._build_final_synthesis(user_message, step_results, ...)
```

### 3.3 工具调用方式

**ReAct Agent**:
- 模型直接输出JSON工具调用
- 支持Function Calling（API模型）和文本JSON（浏览器模型）
- 本地执行，结果直接追加到消息历史

**Plan-and-Execute Agent**:
- 子Agent内部使用ReAct循环
- 编排器负责任务分配和结果汇总
- 支持跨Agent上下文传递（技术栈、已创建文件等）

---

## 四、关键设计模式对比

### 4.1 错误处理

**ReAct Agent**:
- 工具执行异常 → 返回错误信息，继续循环
- JSON解析失败 → 重试一次，仍失败则报错
- 模型幻觉 → 检测并强制要求重新调用工具

**Plan-and-Execute Agent**:
- 步骤失败 → 标记为error，跳过该步骤
- 步骤未完成 → 插入continue_step重试
- 全部失败 → 回退到单Agent模式

### 4.2 上下文管理

**ReAct Agent**:
- 完整消息历史（可能很长）
- 工具结果直接追加到消息
- 无跨会话持久化

**Plan-and-Execute Agent**:
- 步骤间上下文摘要（`_build_step_context`）
- 提取技术栈、已创建文件等关键信息
- 限制上下文长度，避免token爆炸

### 4.3 流式输出

**ReAct Agent**:
- 浏览器模型：边生成边输出到UI
- API模型：Function Calling模式下先执行工具再输出

**Plan-and-Execute Agent**:
- 子Agent事件透传（sub_agent_chunk, sub_agent_done）
- 计划状态实时更新（plan_step_update, plan_replan）
- 最终结果流式汇总（synthesize_chunk）

---

## 五、性能与成本对比

| 指标 | ReAct Agent | Plan-and-Execute Agent |
|------|-------------|------------------------|
| **LLM调用次数** | 每步1次 + 重试 | 1次分析 + N次执行 + 1次汇总 |
| **Token消耗** | 较高（完整历史） | 较低（摘要上下文） |
| **执行时间** | 取决于步数 | 取决于计划复杂度 |
| **可中断性** | 支持stop_event | 支持stop_event |
| **成本可控性** | 较低（可能无限循环） | 较高（计划步骤有限） |

---

## 六、项目中的协同使用

在 `services/core/chat_service.py` 中，两种模式协同工作：

```python
# 简单任务 → ReAct Agent直接处理
if not is_complex:
    yield from self._agent_service.run_stream(...)

# 复杂任务 → Plan-and-Execute多Agent协作
else:
    yield from self._orchestrator.run(...)
```

**路由策略**:
1. 用户输入 → Orchestrator分析复杂度
2. 简单任务 → 直接调用 `AgentService.run_stream()`
3. 复杂任务 → 生成计划 → 多Agent执行 → 汇总结果

---

## 七、总结

### ReAct Agent 优势:
- 灵活性高，适应性强
- 适合探索性、开放式任务
- 实现简单，易于调试

### ReAct Agent 劣势:
- 可能无限循环消耗token
- 缺乏全局规划，可能走弯路
- 成本不可控

### Plan-and-Execute Agent 优势:
- 可预测性强，成本可控
- 支持多Agent协作
- 适合复杂、多步任务

### Plan-and-Execute Agent 劣势:
- 初始计划可能不准确
- 重规划可能引入额外开销
- 实现复杂度高

### 最佳实践:
- **简单任务**（问答、单文件操作）→ ReAct Agent
- **复杂任务**（项目开发、多文件协作）→ Plan-and-Execute Agent
- **混合模式**（项目中使用）→ 根据复杂度自动路由

---

## 八、参考代码位置

| 文件 | 描述 |
|------|------|
| `services/core/agent_service.py` | ReAct Agent核心实现（1500行） |
| `services/multi_agent/orchestrator.py` | Plan-and-Execute编排器（62KB） |
| `services/multi_agent/sub_agent.py` | 子Agent执行器 |
| `services/multi_agent/models.py` | 数据模型（Plan, PlanStep等） |
| `services/config/agent_config.json` | ReAct配置 |
| `services/config/multi_agent_config.json` | 多Agent配置 |

---

*文档生成时间: 2025年*
*基于项目实际代码分析*
