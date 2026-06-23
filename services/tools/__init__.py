"""
工具模块
为 LangChain Agent 提供可调用的工具
"""

from services.tools.file_tools import read_file, write_file, list_directory, search_files, run_command, set_rollback_manager
from services.tools.web_tools import http_request, fetch_webpage, api_call, download_file
from services.tools.data_tools import read_csv, read_excel, analyze_data, create_chart, transform_data
from services.tools.workflow_tools import (workflow_start, workflow_set_variable, workflow_get_variable,
                                           workflow_get_status, execute_sequence, execute_parallel,
                                           execute_conditional, execute_loop)
from services.tools.document_tools import parse_pdf, parse_word, parse_ppt, parse_document, extract_document_metadata

__all__ = [
    # 文件工具
    "read_file", "write_file", "list_directory", "search_files", "run_command", "set_rollback_manager",
    # 网络工具
    "http_request", "fetch_webpage", "api_call", "download_file",
    # 数据分析工具
    "read_csv", "read_excel", "analyze_data", "create_chart", "transform_data",
    # 工具链编排
    "workflow_start", "workflow_set_variable", "workflow_get_variable", "workflow_get_status",
    "execute_sequence", "execute_parallel", "execute_conditional", "execute_loop",
    # 文档解析工具
    "parse_pdf", "parse_word", "parse_ppt", "parse_document", "extract_document_metadata"
]


def get_all_tools():
    """获取所有可用工具列表"""
    return [
        # 文件工具
        read_file, write_file, list_directory, search_files, run_command,
        # 网络工具
        http_request, fetch_webpage, api_call, download_file,
        # 数据分析工具
        read_csv, read_excel, analyze_data, create_chart, transform_data,
        # 工具链编排
        workflow_start, workflow_set_variable, workflow_get_variable, workflow_get_status,
        execute_sequence, execute_parallel, execute_conditional, execute_loop,
        # 文档解析工具
        parse_pdf, parse_word, parse_ppt, parse_document, extract_document_metadata
    ]
