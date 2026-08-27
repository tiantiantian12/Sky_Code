"""
工具模块
为 LangChain Agent 提供可调用的工具
"""

from services.tools.file_tools import read_file, write_file, edit_file, delete_file, list_directory, scan_project, deep_read_directory, search_files, run_command, execute_code, set_rollback_manager, set_code_feedback_callback, set_diff_callback, invalidate_scan_cache, get_cached_project_overview, warm_scan_cache
from services.tools.web_tools import http_request, fetch_webpage, api_call, download_file, web_search
from services.tools.data_tools import read_csv, read_excel, analyze_data, create_chart, transform_data
from services.tools.workflow_tools import (workflow_start, workflow_set_variable, workflow_get_variable,
                                           workflow_get_status, execute_sequence, execute_parallel,
                                           execute_conditional, execute_loop)
from services.tools.document_tools import parse_pdf, parse_word, parse_ppt, parse_document, extract_document_metadata
from services.tools.rag_tools import search_code, get_index_status, warm_rag_and_scan, get_rag_status_for_prompt
# LSP 工具（语言服务协议）
from services.tools.lsp_tools import (diagnose_file, go_to_definition, get_hover_info,
                                       find_references, get_document_symbols, format_code)
# 代码审查 & Bug Finder 工具
from services.tools.code_review_tools import (review_code, find_bugs, security_scan,
                                               check_code_smells, get_code_metrics)
# 调试器工具
from services.tools.debugger_tools import (debug_script, set_breakpoint, get_call_stack,
                                            run_with_trace)
# 终端输出工具
from services.tools.terminal_tools import (get_terminal_output, get_command_history,
                                            clear_terminal, get_terminal_manager)
# 后台 Agent 工具
from services.tools.background_tools import (start_background_task, get_task_status,
                                              list_background_tasks, cancel_background_task)
# Web 预览工具
from services.tools.preview_tools import (start_preview_server, stop_preview_server,
                                           list_preview_servers, preview_in_browser,
                                           open_in_external_browser, set_preview_url_callback)

__all__ = [
    # 文件工具
    "read_file", "write_file", "edit_file", "delete_file", "list_directory", "scan_project", "deep_read_directory", "search_files", "run_command", "execute_code", "set_rollback_manager", "set_code_feedback_callback", "set_diff_callback", "warm_scan_cache",
    # 网络工具
    "http_request", "fetch_webpage", "api_call", "download_file", "web_search",
    # 数据分析工具
    "read_csv", "read_excel", "analyze_data", "create_chart", "transform_data",
    # 工具链编排
    "workflow_start", "workflow_set_variable", "workflow_get_variable", "workflow_get_status",
    "execute_sequence", "execute_parallel", "execute_conditional", "execute_loop",
    # 文档解析工具
    "parse_pdf", "parse_word", "parse_ppt", "parse_document", "extract_document_metadata",
    # RAG 工具
    "search_code", "get_index_status",
    # 自动索引
    "warm_rag_and_scan", "get_rag_status_for_prompt",
    # LSP 工具
    "diagnose_file", "go_to_definition", "get_hover_info", "find_references",
    "get_document_symbols", "format_code",
    # 代码审查 & Bug Finder 工具
    "review_code", "find_bugs", "security_scan", "check_code_smells", "get_code_metrics",
    # 调试器工具
    "debug_script", "set_breakpoint", "get_call_stack", "run_with_trace",
    # 终端输出工具
    "get_terminal_output", "get_command_history", "clear_terminal", "get_terminal_manager",
    # 后台 Agent 工具
    "start_background_task", "get_task_status", "list_background_tasks", "cancel_background_task",
    # Web 预览工具
    "start_preview_server", "stop_preview_server", "list_preview_servers",
    "preview_in_browser", "open_in_external_browser", "set_preview_url_callback",
]


def get_all_tools():
    """获取所有可用工具列表"""
    return [
        # 文件工具
        read_file, write_file, edit_file, delete_file, list_directory, scan_project, deep_read_directory, search_files, run_command, execute_code,
        # 网络工具
        http_request, fetch_webpage, api_call, download_file, web_search,
        # 数据分析工具
        read_csv, read_excel, analyze_data, create_chart, transform_data,
        # 工具链编排
        workflow_start, workflow_set_variable, workflow_get_variable, workflow_get_status,
        execute_sequence, execute_parallel, execute_conditional, execute_loop,
        # 文档解析工具
        parse_pdf, parse_word, parse_ppt, parse_document, extract_document_metadata,
        # RAG 工具
        search_code, get_index_status,
        # LSP 工具
        diagnose_file, go_to_definition, get_hover_info, find_references,
        get_document_symbols, format_code,
        # 代码审查 & Bug Finder 工具
        review_code, find_bugs, security_scan, check_code_smells, get_code_metrics,
        # 调试器工具
        debug_script, set_breakpoint, get_call_stack, run_with_trace,
        # 终端输出工具
        get_terminal_output, get_command_history, clear_terminal,
        # 后台 Agent 工具
        start_background_task, get_task_status, list_background_tasks, cancel_background_task,
        # Web 预览工具
        start_preview_server, stop_preview_server, list_preview_servers,
        preview_in_browser,  open_in_external_browser,
    ]
