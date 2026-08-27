"""
多Agent协作模块 - 编排器
负责任务分解、Agent路由、并行/串行执行和结果合并
"""
from __future__ import annotations
import time
import json
import threading
import logging
from typing import Generator, Optional

from services.multi_agent.models import (
    SubAgentDef, SubTask, SubTaskResult, OrchestrationResult,
    Plan, PlanStep,
)
from services.multi_agent.registry import get_registry
from services.multi_agent.sub_agent import SubAgentExecutor
from services.core.api_service import chat_completion


logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    多Agent编排器

    执行流程:
        1. 分析用户请求 → 判断复杂度
        2. 关键词匹配/LLM分析 → 选择子Agent
        3. 分解子任务 → 分配Agent
        4. 串行/并行执行
        5. 合并结果 → 最终回答
    """

    def __init__(self, agent_service, config: dict = None):
        """
        Args:
            agent_service: AgentService 实例
            config: multi_agent_config 中的 global 配置
        """
        self._agent_service = agent_service
        self._registry = get_registry()
        self._tool_map = {t.name: t for t in agent_service._tools}
        self._config = config or {}

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", True)

    @property
    def _parallel(self) -> bool:
        return self._config.get("parallel_execution", False)

    @property
    def _route_strategy(self) -> str:
        return self._config.get("route_strategy", "hybrid")

    @property
    def _plan_mode(self) -> str:
        """规划模式: 'none'=直接路由, 'replan'=完整RePlan"""
        return self._config.get("plan_mode", "none")

    @property
    def _max_replan_rounds(self) -> int:
        return self._config.get("max_replan_rounds", 3)

    @property
    def _synthesize_with_llm(self) -> bool:
        return self._config.get("synthesize_with_llm", True)

    def run(
        self,
        user_message: str,
        model_display: str = "MiMo-V2-Flash",
        history: list = None,
        app_session_id: str = None,
        workspace_path: str = None,
        stop_event: Optional[threading.Event] = None,
        status_callback=None,
        max_steps: int = None,
    ) -> Generator:
        """
        执行多Agent协作，yield 事件供 UI 展示。

        新流程:
        1. LLM 分析复杂度 → 一次调用同时判断复杂度 + 生成计划
        2. 简单任务 → 直接 AgentService.run_stream() 流式回答
        3. 复杂任务 → 展示计划 → RePlan 多Agent逐步执行
        """
        start_time = time.time()

        if not self._registry._agents:
            logger.warning("没有注册任何子Agent，回退到单Agent模式")
            yield from self._fallback_to_single(
                user_message, model_display, history,
                app_session_id, workspace_path, stop_event, status_callback
            )
            return

        # ── 阶段0: 让模型先自然回应，再判断任务复杂度 ──
        yield {"type": "thought", "output": "正在分析你的请求..."}
        ack, analysis = self._quick_ack_and_classify(
            user_message, model_display, app_session_id, stop_event,
        )
        if ack:
            yield {"type": "thought", "output": ack}
            yield {"type": "result_chunk", "output": ack}

        is_complex = analysis.get("is_complex", False)
        reason = analysis.get("reason", "")

        # ── 简单任务：直接 AgentService 流式回答 ──
        if not is_complex:
            yield {"type": "thought", "output": "任务较简单，我直接开始处理。"}
            yield {
                "type": "orchestrator_routing",
                "output": f"📝 {reason or '简单任务，直接处理'}",
                "agents": [],
                "reason": reason or "简单任务",
            }
            yield from self._run_simple_chat(
                user_message, model_display, history,
                app_session_id, workspace_path, stop_event, status_callback,
                max_steps=max_steps,
            )
            return

        # ── 复杂任务：展示计划 + RePlan 多Agent执行 ──
        plan_data = analysis.get("plan", {})
        raw_steps = plan_data.get("steps", [])
        tech_stack = plan_data.get("tech_stack", "")


        # 解析计划步骤
        plan_steps = []
        for i, s in enumerate(raw_steps):
            plan_steps.append(PlanStep(
                id=f"step_{i}",
                description=s.get("description", f"步骤 {i+1}"),
                agent_name=s.get("agent_name", "general_agent"),
            ))

        if not plan_steps:
            logger.warning("复杂度分析判定为复杂但未生成有效步骤，回退")
            yield from self._run_simple_chat(
                user_message, model_display, history,
                app_session_id, workspace_path, stop_event, status_callback,
                max_steps=max_steps,
            )
            return

        # 如果计划只有1步，补充默认步骤确保多步执行
        if len(plan_steps) < 2:
            logger.warning(f"计划只有 {len(plan_steps)} 步，补充默认步骤")
            plan_steps.append(PlanStep(
                id=f"step_{len(plan_steps)}",
                description="根据前一步的分析结果，执行用户要求的具体操作",
                agent_name="code_agent",
            ))
            plan_steps.append(PlanStep(
                id=f"step_{len(plan_steps)}",
                description="验证操作结果并总结",
                agent_name="general_agent",
            ))

        plan = Plan(steps=plan_steps, version=0)
        if tech_stack:
            plan.tech_stack = tech_stack

        step_count = len(plan_steps)
        logger.info(f"复杂任务计划: {step_count} 个步骤, agents={[s.agent_name for s in plan_steps]}")
        yield {"type": "thought", "output": f"任务需要 {step_count} 个步骤协作完成，我先开始执行第一步。"}
        yield {
            "type": "orchestrator_routing",
            "output": f"🧩 {reason or '复杂任务，启用多Agent协作'}",
            "agents": list(set(s.agent_name for s in plan_steps)),
            "reason": reason or "多Agent协作",
        }

        # 发送计划到 UI
        yield self._build_plan_event("plan_start", plan)

        # ── RePlan 执行 ──
        yield from self._run_replan_with_plan(
            plan, user_message, model_display, history,
            app_session_id, workspace_path, stop_event,
            max_steps=max_steps,
        )

    # ── 内部方法 ─────────────────────────────────────────

    def _route(self, user_message: str) -> list[SubAgentDef]:
        """路由：选择子Agent"""
        if self._route_strategy == "keyword":
            matched = self._registry.match_by_keyword(user_message)
            if not matched:
                fb = self._registry.get_fallback()
                if fb:
                    matched = [fb]
            return matched

        # hybrid: 关键词优先 → LLM 兜底 → 通用 Agent 兜底
        matched = self._registry.match_by_keyword(user_message)
        if matched:
            return matched

        # 关键词未命中，尝试 LLM 路由
        if self._route_strategy == "hybrid":
            llm_matched = self._route_with_llm(user_message)
            if llm_matched:
                logger.info(f"LLM 路由: '{user_message[:50]}...' → {[a.name for a in llm_matched]}")
                return llm_matched

        fb = self._registry.get_fallback()
        if fb:
            matched = [fb]
        return matched

    def _route_with_llm(self, user_message: str) -> list[SubAgentDef]:
        """使用 LLM 判断应该由哪个（哪些）Agent 处理任务"""
        all_agents = self._registry.list_all()
        # 过滤掉兜底 Agent（它不会有 trigger_keywords）
        work_agents = [a for a in all_agents if a.trigger_keywords]
        if not work_agents:
            return []

        agent_descriptions = "\n".join(
            f"- {a.name} ({a.display_name}): {a.role_prompt[:100] if a.role_prompt else '通用工具助手'}"
            for a in work_agents
        )

        prompt = (
            f"你是一个任务路由器。根据用户输入判断应该由哪个（哪些）专业Agent处理。\n\n"
            f"可用Agent：\n{agent_descriptions}\n\n"
            f"用户输入：{user_message}\n\n"
            f"请只输出一个 JSON 数组，包含需要调用的 agent name。\n"
            f"示例: [\"code_agent\"] 或 [\"web_agent\", \"data_agent\"]\n"
            f"如果任务简单或无法归类，输出空数组: []\n"
            f"只输出 JSON，不要其他文字。"
        )

        try:
            from services.core.api_service import chat_completion
            response = ""
            for chunk in chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=None,  # 使用默认模型
                temperature=0.1,
                max_tokens=200,
            ):
                response += chunk

            import re, json as _json
            m = re.search(r'\[.*?\]', response.strip(), re.DOTALL)
            if not m:
                return []
            agent_names = _json.loads(m.group(0))
            if not isinstance(agent_names, list):
                return []

            result = []
            for name in agent_names:
                if not isinstance(name, str):
                    continue
                agent = self._registry.get(name)
                if agent:
                    result.append(agent)
            return result[:3]  # 最多 3 个
        except Exception as e:
            logger.warning(f"LLM 路由失败: {e}")
            return []

    # ── 新增：LLM 复杂度分析 + 计划生成（合并调用） ──

    def _analyze_and_plan(
        self, user_message: str, model_display: str,
        app_session_id: str, stop_event,
    ) -> dict:
        """
        一次 LLM 调用完成两件事：
        1. 判断任务复杂度
        2. 如果复杂，同时生成执行计划

        Returns:
            {"is_complex": bool, "reason": str, "plan": {"tech_stack": str, "steps": [...]}}
        """
        all_agents = self._registry.list_all()
        agent_info = "\n".join([
            f"- {a.name} ({a.display_name}): {a.role_prompt[:100] if a.role_prompt else '通用工具助手'}"
            for a in all_agents
        ])

        prompt = (
            "你是一个任务分析专家。请分析用户请求的复杂度，判断是否需要多Agent协作。\n\n"
            f"## 用户请求\n{user_message}\n\n"
            f"## 可用 Agent\n{agent_info}\n\n"
            "## 判断标准\n"
            "- **简单任务**：纯知识问答、闲聊、单一操作、简单解释等，不需要多步骤或多工具协作\n"
            "- **复杂任务**：需要多步骤执行、涉及多个专业领域、需要生成/修改文件、需要搜索+分析等\n\n"
            "## 输出格式\n请严格以 JSON 格式输出，不要包含任何其他文字：\n"
            '{\n'
            '  "is_complex": true/false,\n'
            '  "reason": "一句话说明判断理由",\n'
            '  "plan": {  // 仅 is_complex=true 时需要\n'
            '    "tech_stack": "推荐的技术栈（如 Python+PyQt5），仅代码类任务需要，其他留空",\n'
            '    "steps": [\n'
            '      {"description": "步骤描述（中文，具体可执行）", "agent_name": "xxx"}\n'
            '    ]\n'
            '  }\n'
            '}\n\n'
            "要求：\n"
            "1. 步骤数量控制在 2-5 个，按执行顺序排列\n"
            "2. agent_name 必须从可用 Agent 列表中选择\n"
            "3. description 要具体、可执行，包含关键技术栈名称\n"
            "4. 简单任务 is_complex=false 时不需要 plan 字段\n"
            "5. 只输出 JSON，不要有任何解释或额外文字"
        )

        try:
            from services.core.api_service import chat_completion
            response = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model_display,
                temperature=0.2,
                max_tokens=4096,
                app_session_id=app_session_id,
            )
            raw = response or ""
            logger.info(f"复杂度分析原始输出: {raw[:500]}")
            return self._parse_analysis_json(raw, user_message)
        except Exception as e:
            logger.warning(f"复杂度分析失败: {e}，回退为简单任务")
            return {"is_complex": False, "reason": "分析失败，按简单任务处理"}

    def _parse_analysis_json(self, raw: str, user_message: str = "") -> dict:
        """
        解析 LLM 返回的复杂度分析 JSON。
        支持 Markdown 代码块、<thinking> 标签、前后有解释文字等脏输出。
        如果仍然无法解析，按关键词启发式回退。
        """
        import re

        if not raw or not isinstance(raw, str):
            logger.warning("复杂度分析返回为空或非字符串")
            return self._heuristic_analysis(user_message, "模型返回为空")

        # 1. 先尝试从 Markdown 代码块中提取 JSON
        code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw, re.IGNORECASE)
        candidates = []
        if code_block_match:
            candidates.append(code_block_match.group(1).strip())

        # 2. 去掉 <thinking>...</thinking> 或 <think>...</think> 等思考标签
        cleaned = re.sub(r'<(thinking|think|reasoning)[^>]*>[\s\S]*?</\1>', '', raw, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        candidates.append(cleaned)

        # 3. 尝试从文本中提取最外层 { ... } JSON 对象
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if json_match:
            candidates.append(json_match.group().strip())
        if cleaned and cleaned != raw.strip():
            json_match2 = re.search(r'\{[\s\S]*\}', cleaned)
            if json_match2:
                candidates.append(json_match2.group().strip())

        # 4. 逐一尝试解析
        for candidate in candidates:
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                if not isinstance(data, dict):
                    continue
                is_complex = bool(data.get("is_complex", False))
                reason = data.get("reason", "")
                plan_data = data.get("plan", {}) if is_complex else {}
                if not isinstance(plan_data, dict):
                    plan_data = {}
                return {
                    "is_complex": is_complex,
                    "reason": reason or ("复杂任务" if is_complex else "简单任务"),
                    "plan": plan_data,
                    "ack": data.get("ack", ""),
                }

            except (json.JSONDecodeError, ValueError):
                continue

        # 5. 全部失败 → 关键词启发式回退
        logger.warning(f"复杂度分析无法解析JSON，启用启发式回退。raw={raw[:200]}")
        return self._heuristic_analysis(user_message, "JSON解析失败，启用关键词启发式")

    def _heuristic_analysis(self, user_message: str, reason: str) -> dict:
        """基于关键词的启发式复杂度判断（JSON解析失败时回退）"""
        complex_keywords = [
            "程序", "代码", "项目", "文件", "创建", "编写", "开发", "实现",
            "修改", "修复", "重构", "优化", "未完成", "继续", "完成", "功能",
            "工具", "脚本", "应用", "app", "python", "pyqt", "qt", "桌面",
            "爬虫", "数据库", "接口", "api", "网页", "网站", "前端", "后端",
        ]
        lower_msg = user_message.lower()
        is_complex = any(kw in lower_msg for kw in complex_keywords)
        return {
            "is_complex": is_complex,
            "reason": f"{reason}，判定为{'复杂' if is_complex else '简单'}任务",
            "plan": {} if not is_complex else {
                "tech_stack": "",
                "steps": [
                    {"description": "分析用户需求并确定实现方案", "agent_name": "general_agent"},
                    {"description": "根据需求编写/修改相关代码或文件", "agent_name": "code_agent"},
                    {"description": "验证结果并总结输出", "agent_name": "general_agent"},
                ]
            },
            "ack": "",
        }


    def _quick_ack_and_classify(
        self,
        user_message: str,
        model_display: str,
        app_session_id: str,
        stop_event,
    ) -> tuple[str, dict]:
        """
        一次 LLM 调用：让模型先给出自然回应，再判断任务复杂度并生成计划。
        返回 (ack, analysis)，ack 可直接进入主消息气泡显示。
        """
        all_agents = self._registry.list_all()
        agent_info = "\n".join([
            f"- {a.name} ({a.display_name}): {a.role_prompt[:80] if a.role_prompt else '通用工具助手'}"
            for a in all_agents
        ])

        prompt = (
            "你是一个任务分析专家。请先做一句自然的中文回应，然后分析用户请求复杂度。"
            "只输出一个JSON对象，不要包含任何其他文字。\n\n"
            f"## 用户请求\n{user_message}\n\n"
            f"## 可用 Agent\n{agent_info}\n\n"
            "## 判断标准\n"
            "- 简单任务：纯问答、闲聊、单一操作，不需要多步骤或多工具协作\n"
            "- 复杂任务：需要多步骤执行、生成/修改文件、搜索+分析、多领域协作\n\n"
            "## 输出格式\n"
            '{\n'
            '  "ack": "一句简短的自然中文回应（如：好的，我来为你继续未完成的桌面宠物程序。），不是最终答案，不要在这里回答用户问题。",\n'
            '  "is_complex": true/false,\n'
            '  "reason": "一句话说明判断理由",\n'
            '  "plan": {  // 仅 is_complex=true 时需要\n'
            '    "tech_stack": "推荐技术栈（代码类任务），其他留空",\n'
            '    "steps": [\n'
            '      {"description": "步骤描述（中文，具体可执行）", "agent_name": "xxx"},\n'
            '      {"description": "步骤描述", "agent_name": "xxx"}\n'
            '    ]\n'
            '  }\n'
            '}\n\n'
            "## 要求\n"
            "1. is_complex=true 时，steps 数组必须包含 2-5 个步骤，按执行顺序排列\n"
            "2. 每个步骤必须是一个独立的、可执行的操作，不要把多个操作合并为一个步骤\n"
            "3. 步骤描述要具体，如'扫描项目结构'、'读取main.py文件'、'修改GIF加载路径'\n"
            "4. agent_name 必须从可用 Agent 列表中选择\n"
            "5. 最后一个步骤应该是验证或总结步骤\n"
        )

        try:
            response = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model_display,
                temperature=0.2,
                max_tokens=4096,
                app_session_id=app_session_id,
                stop_event=stop_event,
            )
        except Exception as e:
            logger.warning(f"快速回应分析失败: {e}，使用兜底回应")
            return "好的，我来处理你的请求。", {
                "is_complex": False,
                "reason": "分析失败，按简单任务处理",
                "plan": {},
                "ack": "",
            }

        raw = response or ""
        logger.info(f"快速回应分析原始输出: {raw[:500]}")
        analysis = self._parse_analysis_json(raw, user_message)
        ack = analysis.get("ack", "")
        if not ack:
            ack = "好的，我来处理你的请求。"
        return ack, analysis


    def _run_simple_chat(
        self, user_message, model_display, history,
        app_session_id, workspace_path, stop_event, status_callback,
        max_steps=None,
    ) -> Generator:

        """简单任务：直接使用 AgentService 流式回答（保留工具调用能力，但无多Agent开销）"""
        for event in self._agent_service.run_stream(
            user_message, model_display,
            history=history,
            app_session_id=app_session_id,
            status_callback=status_callback,
            workspace_path=workspace_path,
            stop_event=stop_event,
            max_steps=max_steps,
        ):
            if event["type"] == "result":
                yield {"type": "orchestrator_done", "result": None}
            elif event["type"] == "result_chunk":
                yield {"type": "synthesize_chunk", "content": event.get("output", "")}
            elif event["type"] == "result_clear":
                yield event
            elif event["type"] == "thinking":
                yield event
            elif event["type"] == "thought":
                yield event
            elif event["type"] == "step":
                yield event
            elif event["type"] == "code_event":
                yield event
            elif event["type"] == "error":
                yield event
            else:
                yield event

    def _run_replan_with_plan(
        self, plan: Plan, user_message: str, model_display: str,
        history: list, app_session_id: str, workspace_path: str,
        stop_event,
        max_steps: int = None,
    ) -> Generator:
        """
        使用预生成的计划执行 RePlan 流程（跳过 _generate_plan，直接用已有计划）。
        最多两轮执行，确保未完成的步骤有重试机会。
        """
        step_results: list[dict] = []
        finished_step_ids: set = set()  # 已执行完成的步骤 ID，避免 result 重复追加
        step_idx = 0

        for retry_round in range(2):  # 最多两轮：第一轮执行 + 一轮重试
            while step_idx < len(plan.steps):
                step = plan.steps[step_idx]
                if stop_event and stop_event.is_set():
                    break

                if step.status in ("done", "error"):
                    step_idx += 1
                    continue

                logger.info(f"RePlan 执行步骤 {step_idx}/{len(plan.steps)}: [{step.agent_name}] {step.description[:80]}")

                try:
                    agent_def = self._registry.get(step.agent_name)
                    if not agent_def:
                        agent_def = self._registry.get_fallback()
                    if not agent_def:
                        step.status = "error"
                        step.result = f"未找到 Agent: {step.agent_name}"
                        yield self._build_plan_event("plan_step_update", plan, step_idx)
                        step_idx += 1
                        continue

                    step.agent_display = agent_def.display_name
                    step.status = "running"
                    yield self._build_plan_event("plan_step_update", plan, step_idx)

                    enhanced_task = step.description
                    if step_results:
                        prev_context = self._build_step_context(step_results, step_idx, user_message)
                        enhanced_task = (
                            f"{prev_context}\n\n"
                            f"【你的任务】{step.description}\n\n"
                            f"重要提醒：\n"
                            f"- 请基于前面步骤的成果继续工作，不要重新开始\n"
                            f"- 如果前面已创建文件或选择了技术栈，请沿用不要改变\n"
                            f"- 如果前面步骤已生成了代码框架，你只需要在其基础上扩展\n"
                            f"- 完成任务后输出总结，不要反问用户是否继续"
                        )

                    executor = SubAgentExecutor(agent_def, self._agent_service, self._tool_map)
                    output = ""
                    error = ""
                    duration_ms = 0

                    for event in executor.execute(
                        task=enhanced_task, task_id=step.id,
                        model_display=model_display, history=history,
                        app_session_id=app_session_id,
                        workspace_path=workspace_path,
                        stop_event=stop_event,
                        max_steps_override=max_steps,
                    ):
                        yield event
                        if event["type"] == "sub_agent_done":
                            output = event.get("output", "")
                            duration_ms = event.get("duration_ms", 0)
                        elif event["type"] == "sub_agent_error":
                            error = event.get("error", "")
                            duration_ms = event.get("duration_ms", 0)

                    is_complete, incomplete_reason = self._check_agent_completion(output)
                    # 对于多步骤计划的中间步骤，放宽完成判定标准
                    # 中间步骤只需要有输出即可，不要求输出长度或不能有反问句
                    is_last_step = (step_idx >= len(plan.steps) - 1)
                    if not is_last_step and not is_complete and not error:
                        # 中间步骤：只要 SubAgent 没有报错且产生了输出，就视为完成
                        # 让编排器继续执行下一步，避免在中间步骤上浪费重试轮次
                        if output and len(output.strip()) > 0:
                            is_complete = True
                            incomplete_reason = ""
                            logger.info(f"RePlan 步骤 {step_idx} 是中间步骤，放宽完成判定（output_len={len(output)}）")
                    if error:
                        step.status = "error"
                        step.result = error
                    elif not is_complete and not error:
                        step.status = "incomplete"
                        step.result = output[:8000]
                    else:
                        step.status = "done"
                        step.result = output[:8000]

                    # 记录结果（重试时用最新输出覆盖旧记录）
                    entry = {
                        "step_id": step.id,
                        "description": step.description,
                        "agent_display": agent_def.display_name,
                        "output": output,
                        "success": not bool(error),
                        "is_complete": is_complete,
                        "incomplete_reason": incomplete_reason if not is_complete else "",
                        "error": error,
                        "duration_ms": duration_ms,
                    }
                    existing_idx = next((i for i, r in enumerate(step_results) if r["step_id"] == step.id), -1)
                    if existing_idx >= 0:
                        step_results[existing_idx] = entry
                    else:
                        step_results.append(entry)
                    if step.status == "done":
                        finished_step_ids.add(step.id)

                    logger.info(f"RePlan 步骤 {step_idx} 完成: status={step.status}, output_len={len(output)}, error={bool(error)}")

                    yield self._build_plan_event("plan_step_update", plan, step_idx)

                    # 重规划：仅在步骤未完成（但非error）时触发
                    # error 步骤直接跳过，不再插入 continue_step，避免无限重试推后后续步骤
                    should_replan = not is_complete and not error and not (stop_event and stop_event.is_set())
                    if should_replan:
                        remaining_steps = plan.steps[step_idx + 1:]
                        continue_step = PlanStep(
                            id=f"step_{step_idx}_continue",
                            description=(
                                f"继续完成上一任务: {step.description}\n"
                                f"上一轮Agent未能完成任务（{incomplete_reason}），"
                                f"请直接执行操作，完成任务后输出结果，不要再反问用户。"
                            ),
                            agent_name=step.agent_name,
                        )

                        # 不调用 LLM 重规划（避免丢弃后续步骤），
                        # 直接在原始后续步骤前插入 continue_step
                        plan.steps = plan.steps[:step_idx + 1] + [continue_step] + remaining_steps
                        plan.version += 1
                        yield self._build_plan_event("plan_replan", plan)
                        logger.info(f"RePlan: 步骤 {step_idx} 未完成，插入 continue_step，保留 {len(remaining_steps)} 个原始后续步骤")

                except Exception as step_exc:
                    logger.error(f"RePlan 步骤 {step_idx} 执行异常: {step_exc}", exc_info=True)
                    step.status = "error"
                    step.result = f"步骤执行异常: {step_exc}"
                    output = ""
                    error = str(step_exc)
                    try:
                        _display = agent_def.display_name
                    except (NameError, AttributeError):
                        _display = step.agent_name
                    step_results.append({
                        "step_id": step.id,
                        "description": step.description,
                        "agent_display": _display,
                        "output": "",
                        "success": False,
                        "is_complete": False,
                        "incomplete_reason": "步骤执行异常",
                        "error": error,
                        "duration_ms": 0,
                    })
                    yield self._build_plan_event("plan_step_update", plan, step_idx)

                step_idx += 1

            # 检查是否有未完成的步骤需要重试
            pending = [s for s in plan.steps if s.status not in ("done", "error")]
            if not pending or (stop_event and stop_event.is_set()):
                break
            if retry_round == 0:  # 第一轮结束，还有未完成步骤 → 重试
                logger.warning(
                    f"计划第 {retry_round+1} 轮结束，仍有 {len(pending)} 个未完成步骤: "
                    f"{[s.description[:40] for s in pending]}，开始重试..."
                )
                yield {"type": "thought", "output": f"检测到 {len(pending)} 个步骤未完成，正在重试..."}
                # 重置未完成步骤的状态并通知 UI
                for i, ps in enumerate(plan.steps):
                    if ps.status not in ("done", "error"):
                        ps.status = "pending"
                        yield self._build_plan_event("plan_step_update", plan, i)
                step_idx = 0  # 从 0 开始扫描，done 的会自动跳过

        # ── 汇总输出 ──
        plan.is_complete = True
        # 日志：输出所有步骤的最终状态
        for i, s in enumerate(plan.steps):
            logger.info(f"RePlan 最终状态 步骤{i}: status={s.status}, desc={s.description[:60]}")
        logger.info(f"RePlan 完成: {len(step_results)}/{len(plan.steps)} 步有结果, 版本 v{plan.version}")
        yield self._build_plan_event("plan_done", plan)

        yield {"type": "orchestrator_synthesizing", "output": "正在汇总各步骤结果..."}

        final_answer = self._build_final_synthesis(
            user_message, step_results, model_display,
            app_session_id, stop_event,
        )
        final_text = ""
        for event in final_answer:
            yield event
            if isinstance(event, dict) and event.get("type") == "synthesize_chunk":
                final_text += event.get("content", "")

        duration_ms = 0  # approximate, individual step durations are tracked separately
        orc = OrchestrationResult(
            success=all(r["success"] for r in step_results),
            final_answer=final_text,
            sub_results=[],
            agent_count=len(set(r["agent_display"] for r in step_results)),
            total_duration_ms=duration_ms,
            routing_reason=f"LLM分析+RePlan: {len(plan.steps)} 步, v{plan.version}",
        )
        yield {"type": "orchestrator_done", "result": orc}

    def _execute_sub_agent(
        self,
        agent_def: SubAgentDef,
        task: str,
        model_display: str,
        history: list,
        app_session_id: str,
        workspace_path: str,
        stop_event,
        results: list,
    ) -> Generator:
        """执行单个子Agent并收集结果"""
        task_id = f"sub_{agent_def.name}_{len(results)}"
        executor = SubAgentExecutor(agent_def, self._agent_service, self._tool_map)

        output = ""
        steps = []
        error = ""
        duration_ms = 0

        for event in executor.execute(
            task=task, task_id=task_id,
            model_display=model_display,
            history=history,
            app_session_id=app_session_id,
            workspace_path=workspace_path,
            stop_event=stop_event,
        ):
            yield event
            if event["type"] == "sub_agent_done":
                output = event.get("output", "")
                steps = event.get("steps", [])
                duration_ms = event.get("duration_ms", 0)
            elif event["type"] == "sub_agent_error":
                error = event.get("error", "")
                duration_ms = event.get("duration_ms", 0)

        results.append(SubTaskResult(
            task_id=task_id,
            agent_name=agent_def.name,
            agent_display=agent_def.display_name,
            success=not bool(error),
            output=output,
            steps=steps,
            error=error,
            duration_ms=duration_ms,
        ))

    def _run_single_agent(
        self, agent_def: SubAgentDef, task: str,
        model_display: str, history: list,
        app_session_id: str, workspace_path: str,
        stop_event,
    ) -> Generator:
        """单一Agent处理（简单任务），执行完成后发送 result 事件标记结束"""
        executor = SubAgentExecutor(agent_def, self._agent_service, self._tool_map)
        final_output = ""
        for event in executor.execute(
            task=task, task_id="single",
            model_display=model_display,
            history=history,
            app_session_id=app_session_id,
            workspace_path=workspace_path,
            stop_event=stop_event,
        ):
            yield event
            if event.get("type") == "sub_agent_chunk":
                final_output += event.get("content", "")
        # 子Agent完成后发送空 result 事件标记结束（正文已流式输出）
        yield {"type": "orchestrator_done", "result": OrchestrationResult(
            success=True,
            final_answer=final_output,
            sub_results=[],
            agent_count=1,
            total_duration_ms=0,
            routing_reason=f"单一Agent: {agent_def.display_name}",
        )}

    def _synthesize_results_stream(
        self, user_message: str, results: list[SubTaskResult],
        model_display: str, history: list,
        app_session_id: str, stop_event,
    ) -> Generator:
        """用LLM流式合并总结多个Agent的结果，逐块 yield result_chunk"""
        if not results:
            yield {"type": "synthesize_chunk", "content": "任务执行完成，但没有返回结果。"}
            return

        if len(results) == 1:
            # 单个Agent结果直接作为最终回答
            output = results[0].output
            yield {"type": "synthesize_chunk", "content": output}
            return

        if not self._synthesize_with_llm:
            # 直接拼接（非流式但立刻完成）
            parts = [f"## {r.agent_display}\n{r.output}" for r in results]
            yield {"type": "synthesize_chunk", "content": "\n\n".join(parts)}
            return

        # 用LLM流式总结
        parts = []
        for r in results:
            parts.append(f"【{r.agent_display}】\n{r.output[:8000]}")
        synthesis_prompt = (
            f"用户原始问题：{user_message}\n\n"
            f"以下是由多个专业Agent分别执行后返回的结果，请整合为一份连贯、简洁的最终回答：\n\n"
            + "\n\n".join(parts) +
            "\n\n请用中文整合以上所有信息，给出一个完整的回答。"
        )

        try:
            from services.core.api_service import chat_completion_stream, find_model_by_display
            model_info = find_model_by_display(model_display)
            model_name = model_info["model"] if model_info else model_display

            for chunk in chat_completion_stream(
                messages=[{"role": "user", "content": synthesis_prompt}],
                model=model_name,
                temperature=0.3,
                max_tokens=2048,
                app_session_id=app_session_id,
            ):
                if stop_event and stop_event.is_set():
                    break
                yield {"type": "synthesize_chunk", "content": chunk}
        except Exception as e:
            logger.warning(f"LLM流式合并总结失败: {e}，使用直接拼接")
            parts = [f"## {r.agent_display}\n{r.output}" for r in results]
            yield {"type": "synthesize_chunk", "content": "\n\n".join(parts)}

    # ── RePlan 模式 ─────────────────────────────────────

    def _run_replan(
        self, user_message, model_display, history,
        app_session_id, workspace_path, stop_event,
    ) -> Generator:
        """
        RePlan 模式完整流程：
        1. LLM 分析请求 → 生成结构化计划
        2. 逐步执行 → 每步结束后评估并重规划
        3. 汇总所有步骤结果 → 流式输出最终回答
        """
        start_time = time.time()

        # 获取可用 Agent 列表供 LLM 参考
        all_agents = self._registry.list_all()
        agent_info = "\n".join([
            f"- {a.name} ({a.display_name}): {a.role_prompt[:80] if a.role_prompt else '通用助手'}"
            for a in all_agents
        ])

        # ── 阶段1: LLM 生成计划 ──
        yield {"type": "orchestrator_analyzing", "output": "正在评估任务规模..."}
        yield {"type": "thinking", "output": "正在制定分步执行计划..."}
        plan = self._generate_plan(
            user_message, agent_info, model_display, history,
            app_session_id, stop_event,
        )

        if not plan or not plan.steps:
            logger.warning("RePlan 未能生成有效计划，回退到常规模式")
            yield from self._fallback_to_single(
                user_message, model_display, history,
                app_session_id, workspace_path, stop_event, None,
            )
            return

        # 发送计划到 UI
        yield self._build_plan_event("plan_start", plan)

        # ── 阶段2: 逐步执行 + 重规划 ──
        step_results: list[dict] = []  # [{step_id, agent_display, output, success}]
        step_idx = 0

        while step_idx < len(plan.steps):
            step = plan.steps[step_idx]
            if stop_event and stop_event.is_set():
                break

            # 跳过已完成或已出错的步骤
            if step.status in ("done", "error"):
                step_idx += 1
                continue

            try:
                # 查找对应 Agent
                agent_def = self._registry.get(step.agent_name)
                if not agent_def:
                    agent_def = self._registry.get_fallback()
                if not agent_def:
                    step.status = "error"
                    step.result = f"未找到 Agent: {step.agent_name}"
                    yield self._build_plan_event("plan_step_update", plan, step_idx)
                    step_idx += 1
                    continue

                step.agent_display = agent_def.display_name
                step.status = "running"
                yield self._build_plan_event("plan_step_update", plan, step_idx)

                # ── 构建增强任务上下文：注入前序步骤的完成情况 ──
                enhanced_task = step.description
                if step_results:
                    prev_context = self._build_step_context(step_results, step_idx, user_message)
                    enhanced_task = (
                        f"{prev_context}\n\n"
                        f"【你的任务】{step.description}\n\n"
                        f"重要提醒：\n"
                        f"- 请基于前面步骤的成果继续工作，不要重新开始\n"
                        f"- 如果前面已创建文件或选择了技术栈，请沿用不要改变\n"
                        f"- 如果前面步骤已生成了代码框架，你只需要在其基础上扩展\n"
                        f"- 完成任务后输出总结，不要反问用户是否继续"
                    )

                # 执行当前步骤
                executor = SubAgentExecutor(agent_def, self._agent_service, self._tool_map)
                output = ""
                error = ""
                duration_ms = 0

                for event in executor.execute(
                    task=enhanced_task,
                    task_id=step.id,
                    model_display=model_display,
                    history=history,
                    app_session_id=app_session_id,
                    workspace_path=workspace_path,
                    stop_event=stop_event,
                ):
                    yield event  # 透传子Agent事件给 UI
                    if event["type"] == "sub_agent_done":
                        output = event.get("output", "")
                        duration_ms = event.get("duration_ms", 0)
                    elif event["type"] == "sub_agent_error":
                        error = event.get("error", "")
                        duration_ms = event.get("duration_ms", 0)

                # 标记步骤完成（带完成验证）
                is_complete, incomplete_reason = self._check_agent_completion(output)
                # 对于多步骤计划的中间步骤，放宽完成判定标准
                is_last_step = (step_idx >= len(plan.steps) - 1)
                if not is_last_step and not is_complete and not error:
                    if output and len(output.strip()) > 0:
                        is_complete = True
                        incomplete_reason = ""
                if error:
                    step.status = "error"
                    step.result = error
                elif not is_complete and not error:
                    # Agent 输出了反问/未完成，标记为 incomplete
                    step.status = "incomplete"
                    step.result = output[:8000]
                    logger.warning(f"步骤 {step.id} 可能未完成: {incomplete_reason}")
                else:
                    step.status = "done"
                    step.result = output[:8000]
                step_results.append({
                    "step_id": step.id,
                    "description": step.description,
                    "agent_display": agent_def.display_name,
                    "output": output,
                    "success": not bool(error),
                    "is_complete": is_complete,
                    "incomplete_reason": incomplete_reason if not is_complete else "",
                    "error": error,
                    "duration_ms": duration_ms,
                })
                yield self._build_plan_event("plan_step_update", plan, step_idx)

                # ── 阶段2b: 重规划（仅在步骤未完成且非error时触发） ──
                # error 步骤直接跳过，避免无限插入 continue_step 推后后续步骤
                should_replan = not is_complete and not error and not (stop_event and stop_event.is_set())
                if should_replan:
                    remaining_steps = plan.steps[step_idx + 1:]
                    # 上一步未完成：在剩余步骤前插入一个继续执行的步骤
                    continue_step = PlanStep(
                        id=f"step_{step_idx}_continue",
                        description=(
                            f"继续完成上一任务: {step.description}\n"
                            f"上一轮Agent未能完成任务（{incomplete_reason}），"
                            f"请直接执行操作（write_file/run_command等工具），"
                            f"完成任务后输出结果，不要再反问用户。"
                        ),
                        agent_name=step.agent_name,
                    )
                    # 不调用 LLM 重规划（避免丢弃后续步骤），
                    # 直接在原始后续步骤前插入 continue_step
                    plan.steps = plan.steps[:step_idx + 1] + [continue_step] + remaining_steps
                    plan.version += 1
                    yield self._build_plan_event("plan_replan", plan)

            except Exception as step_exc:
                logger.error(f"RePlan 步骤 {step_idx} 执行异常: {step_exc}", exc_info=True)
                step.status = "error"
                step.result = f"步骤执行异常: {step_exc}"
                try:
                    _display = agent_def.display_name
                except (NameError, AttributeError):
                    _display = step.agent_name
                step_results.append({
                    "step_id": step.id,
                    "description": step.description,
                    "agent_display": _display,
                    "output": "",
                    "success": False,
                    "is_complete": False,
                    "incomplete_reason": "步骤执行异常",
                    "error": str(step_exc),
                    "duration_ms": 0,
                })
                yield self._build_plan_event("plan_step_update", plan, step_idx)

            step_idx += 1

        # ── 阶段3: 汇总输出 ──
        plan.is_complete = True
        yield self._build_plan_event("plan_done", plan)

        yield {"type": "orchestrator_synthesizing", "output": "正在汇总各步骤结果..."}

        # 流式生成最终回答
        final_answer = self._build_final_synthesis(
            user_message, step_results, model_display,
            app_session_id, stop_event,
        )
        final_text = ""
        for event in final_answer:
            yield event
            if isinstance(event, dict) and event.get("type") == "synthesize_chunk":
                final_text += event.get("content", "")

        duration_ms = (time.time() - start_time) * 1000
        orc = OrchestrationResult(
            success=all(r["success"] for r in step_results),
            final_answer=final_text,
            sub_results=[],
            agent_count=len(set(r["agent_display"] for r in step_results)),
            total_duration_ms=duration_ms,
            routing_reason=f"RePlan 模式: {len(plan.steps)} 步, {plan.version} 次重规划",
        )
        yield {"type": "orchestrator_done", "result": orc}

    def _generate_plan(
        self, user_message: str, agent_info: str,
        model_display: str, history: list,
        app_session_id: str, stop_event,
    ) -> Optional[Plan]:
        """让 LLM 生成结构化的执行计划"""
        prompt = (
            "你是一个任务规划专家。请分析用户的请求，将其拆解为按顺序执行的具体步骤，"
            "并为每一步指定最合适的执行者(agent)。\n\n"
            f"## 用户请求\n{user_message}\n\n"
            f"## 可用 Agent\n{agent_info}\n\n"
            "## 输出格式\n请严格以 JSON 格式输出，不要包含任何其他文字：\n"
            '{"tech_stack": "推荐的技术栈（如 Python+PyQt5、JavaScript+Electron 等）",'
            '"steps": [{"description": "步骤描述", "agent_name": "xxx"}]}\n\n'
            "要求：\n"
            "1. **必须指定 tech_stack**：根据用户需求分析并推荐最合适的技术栈，步骤描述中要注明使用的语言/框架\n"
            "2. 步骤必须按执行顺序排列，不能并行\n"
            "3. 每个步骤指定一个 agent_name（从可用 Agent 中选择最合适的）\n"
            "4. 步骤数量控制在 2-5 个\n"
            "5. description 要具体、可执行，用中文，**包含具体的技术栈名称**（如'用Python+PyQt5创建主窗口'而非'创建主窗口'）\n"
            "6. 如果一个步骤的 agent 不清楚，用 general_agent\n"
            "7. 只输出 JSON，不要有任何解释或额外文字\n"
            "8. 所有步骤必须使用**相同的技术栈**，后续步骤沿用第一步的技术选择"
        )

        try:
            from services.core.api_service import chat_completion
            content = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model_display,
                temperature=0.2,
                max_tokens=4096,
                app_session_id=app_session_id,
            )
            # chat_completion 直接返回 str 内容
            return self._parse_plan_json(content or "")
        except Exception as e:
            logger.error(f"LLM 生成计划失败: {e}")
            return None

    def _parse_plan_json(self, raw: str) -> Optional[Plan]:
        """解析 LLM 返回的 JSON 计划"""
        import re
        # 尝试提取 JSON 块
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            logger.warning(f"无法从 LLM 回复中提取 JSON: {raw[:200]}")
            return None

        try:
            data = json.loads(json_match.group())
            raw_steps = data.get("steps", [])
            if not raw_steps:
                return None

            steps = []
            for i, s in enumerate(raw_steps):
                steps.append(PlanStep(
                    id=f"step_{i}",
                    description=s.get("description", f"步骤 {i+1}"),
                    agent_name=s.get("agent_name", "general_agent"),
                ))
            return Plan(steps=steps, version=0)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"解析计划 JSON 失败: {e}, raw={raw[:200]}")
            return None

    def _replan(
        self, user_message: str, remaining_steps: list[PlanStep],
        step_results: list[dict], plan_version: int,
        agent_info: str, model_display: str,
        app_session_id: str, stop_event,
    ) -> Optional[list[PlanStep]]:
        """
        根据已完成步骤的结果，重新规划剩余步骤。
        返回 None 表示不需要调整（保持原计划）。
        """
        if not remaining_steps:
            return None

        # 已完成的步骤摘要
        done_summary = "\n".join([
            f"[{r['agent_display']}] {r['description']}: "
            f"{'成功' if r['success'] else '失败: ' + r.get('error', '')}"
            f" | 结果: {r['output'][:200]}"
            for r in step_results
        ])

        remaining_desc = "\n".join([
            f"  {i+1}. [{s.agent_name}] {s.description}"
            for i, s in enumerate(remaining_steps)
        ])

        prompt = (
            "你是一个任务规划专家。请根据已执行步骤的结果，评估剩余计划是否需要调整。\n\n"
            f"## 用户原始请求\n{user_message}\n\n"
            f"## 已完成步骤\n{done_summary}\n\n"
            f"## 当前剩余计划\n{remaining_desc}\n\n"
            f"## 可用 Agent\n{agent_info}\n\n"
            "## 判断与输出\n"
            "- 如果剩余计划完全合理、无需修改 → 输出: KEEP\n"
            "- 如果需要调整（增减步骤、修改描述、更换agent）→ 输出完整的新剩余步骤 JSON：\n"
            '  {"steps": [{"description": "...", "agent_name": "..."}]}\n'
            "注意：只输出 KEEP 或 JSON，不要输出任何其他文字。"
        )

        try:
            from services.core.api_service import chat_completion
            content = chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model=model_display,
                temperature=0.2,
                max_tokens=800,
                app_session_id=app_session_id,
            )
            # chat_completion 直接返回 str 内容
            content = (content or "").strip()
            if not content or content.upper().startswith("KEEP"):
                return None

            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if not json_match:
                return None

            data = json.loads(json_match.group())
            raw_steps = data.get("steps", [])
            if not raw_steps:
                return None

            base_idx = len(step_results)  # 新步骤从已完成的之后编号
            new_steps = []
            for i, s in enumerate(raw_steps):
                new_steps.append(PlanStep(
                    id=f"step_{base_idx + i}",
                    description=s.get("description", f"步骤 {base_idx + i + 1}"),
                    agent_name=s.get("agent_name", "general_agent"),
                ))
            logger.info(f"RePlan v{plan_version + 1}: {len(remaining_steps)}→{len(new_steps)} 步骤")
            return new_steps
        except Exception as e:
            logger.warning(f"重规划失败: {e}，保持原计划")
            return None

    def _build_final_synthesis(
        self, user_message: str, step_results: list[dict],
        model_display: str, app_session_id: str, stop_event,
    ) -> Generator:
        """汇总所有步骤结果，生成最终回答流式输出"""
        if not step_results:
            yield {"type": "synthesize_chunk", "content": "任务执行完成，但没有返回结果。"}
            return

        # 构建摘要
        steps_text = "\n".join([
            f"步骤{i+1} [{r['agent_display']}]: {r['output'][:8000]}"
            for i, r in enumerate(step_results)
        ])

        synthesis_prompt = (
            f"用户原始问题：{user_message}\n\n"
            f"以下是由多个步骤分别执行后返回的结果，请整合为一份连贯、完整的最终回答。\n"
            f"要求：\n"
            f"1. 不要简单拼接各步骤的输出，要重新组织语言，给出连贯的回答\n"
            f"2. 如果是代码调试/分析类任务，给出具体的问题原因和解决方案\n"
            f"3. 如果是代码编写类任务，说明已创建/修改的文件和关键实现\n"
            f"4. 用 markdown 格式输出，代码块用 ```标注\n\n"
            f"各步骤执行结果：\n{steps_text}"
        )

        try:
            from services.core.api_service import chat_completion_stream
            for chunk in chat_completion_stream(
                messages=[{"role": "user", "content": synthesis_prompt}],
                model=model_display,
                temperature=0.3,
                max_tokens=4096,
                app_session_id=app_session_id,
            ):
                if stop_event and stop_event.is_set():
                    break
                yield {"type": "synthesize_chunk", "content": chunk}
        except Exception as e:
            logger.warning(f"汇总失败: {e}")
            parts = []
            for i, r in enumerate(step_results):
                parts.append(f"## {r['agent_display']}\n{r['output']}")
            yield {"type": "synthesize_chunk", "content": "\n\n".join(parts)}

    def _build_step_context(
        self, step_results: list[dict], current_step_idx: int,
        user_message: str,
    ) -> str:
        """构建注入后续步骤的上下文摘要，包含技术栈、已创建文件等信息"""
        import re as _re

        lines = [f"## 前面已完成的步骤（你是第 {current_step_idx + 1} 步）"]

        for i, r in enumerate(step_results):
            output = r.get("output", "")
            lines.append(f"\n### 步骤{i+1} [{r['agent_display']}]")
            lines.append(f"描述: {r.get('description', '')}")
            lines.append(f"结果: {'成功' if r.get('success') else '失败'}")

            # 提取已创建/修改的文件
            file_patterns = [
                _re.findall(r'(?:创建|写入|修改|生成|保存|created|wrote|written|saved)'
                            r'\s*(?:文件|file)?[:：]?\s*[\'"`]?([^\s\'"`>]+(?:\.[a-zA-Z]+))[\'"`]?',
                            output, _re.I),
                _re.findall(r'[\'"`]([^\'"`]+\.(?:js|py|ts|jsx|tsx|html|css|json|md'
                            r'|yaml|yml|toml|xml|csv|txt))[\'"`]', output, _re.I),
                _re.findall(r'(?:write_file|edit_file|create_file)\s*[:：]\s*[\'"`]?'
                            r'([^\'"`\s]+(?:\.[a-zA-Z]+))[\'"`]?', output, _re.I),
            ]
            all_files = set()
            for matches in file_patterns:
                for m in matches:
                    cleaned = m.strip().replace("```", "")
                    if "." in cleaned and len(cleaned) < 200:
                        all_files.add(cleaned)
            if all_files:
                lines.append(f"已操作文件: {', '.join(sorted(all_files)[:10])}")

            # 提取技术栈/语言选择
            lang_patterns = [
                r'(?:使用|语言|技术栈|用|选择|基于|采用|编程语言)\s*(?:是|为|：|:)?\s*'
                r'(JavaScript|TypeScript|Python|Rust|Go|Java|C\+\+|React|Vue|'
                r'Next\.?js|Node\.?js|PyQt\d?|PySide\d?|Tkinter|Electron|'
                r'Tauri|Flask|FastAPI|Django|Express|jQuery|Qt\s*(?:Widgets|Quick)?)',
                r'(JavaScript|TypeScript|Python|Rust|Go|React|Vue|'
                r'Next\.?js|Node\.?js|PyQt\d?|PySide\d?|Tkinter|Electron|'
                r'Tauri|Flask|FastAPI|Django)\s*(?:项目|框架|技术栈|开发|程序)',
            ]
            techs = set()
            for pattern in lang_patterns:
                found = _re.findall(pattern, output, _re.I)
                techs.update(found)
            if techs:
                lines.append(f"技术栈/语言: {', '.join(sorted(techs))}")

            # 提取关键成就摘要（完整输出前500字）
            truncated = output[:500]
            if len(output) > 500:
                truncated += "..."
            lines.append(f"详细结果: {truncated}")

        lines.append(f"\n## 用户原始需求\n{user_message}")
        return "\n".join(lines)

    def _check_agent_completion(self, output: str) -> tuple[bool, str]:
        """检查Agent是否真正完成了任务，还是只是反问/提问
        返回 (is_complete, reason)"""
        # 检测反问/提问模式（中文和英文）
        question_patterns = [
            r'需要我.*吗[？?]',
            r'是否.*继续[？?]',
            r'需要.*实现.*吗[？?]',
            r'你想要.*吗[？?]',
            r'还有什么.*需要.*吗[？?]',
            r'shall I\b',
            r'would you like me to\b',
            r'do you want\b',
            r'anything else\b',
        ]
        for p in question_patterns:
            if __import__('re').search(p, output, __import__('re').I):
                return False, "Agent输出以反问结尾，任务可能未完成"

        # 检测是否太短（< 50字符大概率没做完）
        if len(output.strip()) < 50:
            return False, "Agent输出过短，可能未执行实际操作"

        return True, ""

    def _build_plan_event(self, action: str, plan: Plan, step_index: int = -1) -> dict:
        """构建计划相关事件"""
        steps_data = []
        for s in plan.steps:
            steps_data.append({
                "id": s.id,
                "description": s.description,
                "agent_name": s.agent_name,
                "agent_display": s.agent_display,
                "status": s.status,
                "result": s.result,
            })

        event = {
            "type": "plan",
            "action": action,
            "plan_version": plan.version,
            "steps": steps_data,
        }
        if step_index >= 0:
            event["step_index"] = step_index
        return event

    def _fallback_to_single(
        self, user_message, model_display, history,
        app_session_id, workspace_path, stop_event, status_callback,
    ) -> Generator:
        """回退到单Agent模式"""
        yield {"type": "orchestrator_routing",
               "output": "未找到匹配的Agent，使用通用模式",
               "agents": [], "reason": "无可用Agent"}
        yield from self._agent_service.run_stream(
            user_message, model_display,
            history=history,
            app_session_id=app_session_id,
            status_callback=status_callback,
            workspace_path=workspace_path,
            stop_event=stop_event,
        )
