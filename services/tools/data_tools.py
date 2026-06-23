"""
数据分析工具集
为 Agent 提供 CSV/Excel 处理、数据可视化能力
"""

import os
import json
import tempfile
from typing import Optional
from langchain_core.tools import tool


@tool
def read_csv(file_path: str, encoding: str = "utf-8", delimiter: str = ",",
             max_rows: int = 100) -> str:
    """读取 CSV 文件并返回数据预览。

    Args:
        file_path: CSV 文件的绝对路径
        encoding: 文件编码（默认 utf-8）
        delimiter: 分隔符（默认逗号）
        max_rows: 最大返回行数（默认 100）
    """
    import pandas as pd

    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        df = pd.read_csv(file_path, encoding=encoding, delimiter=delimiter, nrows=max_rows)

        result = [
            f"文件: {file_path}",
            f"行数: {len(df)}",
            f"列数: {len(df.columns)}",
            f"列名: {', '.join(df.columns.tolist())}",
            "",
            "数据类型:",
            df.dtypes.to_string(),
            "",
            "前 5 行数据:",
            df.head().to_string(),
            "",
            "基本统计:",
            df.describe().to_string()
        ]

        return "\n".join(result)

    except Exception as e:
        return f"错误: 读取 CSV 失败 - {e}"


@tool
def read_excel(file_path: str, sheet_name: Optional[str] = None,
               max_rows: int = 100) -> str:
    """读取 Excel 文件并返回数据预览。

    Args:
        file_path: Excel 文件的绝对路径
        sheet_name: 工作表名称（可选，默认读取第一个）
        max_rows: 最大返回行数（默认 100）
    """
    import pandas as pd

    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        # 获取所有工作表名
        excel_file = pd.ExcelFile(file_path)
        sheet_names = excel_file.sheet_names

        # 读取指定工作表
        df = pd.read_excel(file_path, sheet_name=sheet_name or sheet_names[0], nrows=max_rows)

        result = [
            f"文件: {file_path}",
            f"工作表: {', '.join(sheet_names)}",
            f"当前工作表: {sheet_name or sheet_names[0]}",
            f"行数: {len(df)}",
            f"列数: {len(df.columns)}",
            f"列名: {', '.join(df.columns.tolist())}",
            "",
            "数据类型:",
            df.dtypes.to_string(),
            "",
            "前 5 行数据:",
            df.head().to_string(),
            "",
            "基本统计:",
            df.describe().to_string()
        ]

        return "\n".join(result)

    except Exception as e:
        return f"错误: 读取 Excel 失败 - {e}"


@tool
def analyze_data(file_path: str, analysis_type: str = "summary",
                 column: Optional[str] = None, group_by: Optional[str] = None) -> str:
    """分析 CSV/Excel 数据。

    Args:
        file_path: 数据文件的绝对路径
        analysis_type: 分析类型 - summary(摘要)、correlation(相关性)、groupby(分组统计)、value_counts(值计数)
        column: 指定分析的列名（可选）
        group_by: 分组列名（groupby 分析时使用）
    """
    import pandas as pd

    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        # 根据文件类型读取
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.csv':
            df = pd.read_csv(file_path)
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(file_path)
        else:
            return f"错误: 不支持的文件类型 {ext}"

        if analysis_type == "summary":
            result = [
                "数据摘要:",
                f"形状: {df.shape}",
                f"列名: {', '.join(df.columns.tolist())}",
                "",
                "数据类型:",
                df.dtypes.to_string(),
                "",
                "缺失值:",
                df.isnull().sum().to_string(),
                "",
                "基本统计:",
                df.describe().to_string()
            ]
            if column and column in df.columns:
                result.extend([
                    "",
                    f"列 '{column}' 详细统计:",
                    f"唯一值数量: {df[column].nunique()}",
                    f"最常见值:\n{df[column].value_counts().head(10).to_string()}"
                ])
            return "\n".join(result)

        elif analysis_type == "correlation":
            numeric_df = df.select_dtypes(include=['number'])
            if numeric_df.empty:
                return "错误: 没有数值列可供分析"
            corr_matrix = numeric_df.corr()
            return f"相关性矩阵:\n{corr_matrix.to_string()}"

        elif analysis_type == "groupby":
            if not group_by:
                return "错误: groupby 分析需要指定 group_by 参数"
            if group_by not in df.columns:
                return f"错误: 列 '{group_by}' 不存在"
            if column and column in df.columns:
                grouped = df.groupby(group_by)[column].agg(['mean', 'sum', 'count'])
            else:
                grouped = df.groupby(group_by).agg(['mean', 'sum', 'count'])
            return f"分组统计结果:\n{grouped.to_string()}"

        elif analysis_type == "value_counts":
            if not column:
                return "错误: value_counts 分析需要指定 column 参数"
            if column not in df.columns:
                return f"错误: 列 '{column}' 不存在"
            counts = df[column].value_counts()
            return f"列 '{column}' 值计数:\n{counts.to_string()}"

        else:
            return f"错误: 不支持的分析类型 '{analysis_type}'"

    except Exception as e:
        return f"错误: 数据分析失败 - {e}"


@tool
def create_chart(file_path: str, chart_type: str = "bar",
                 x_column: Optional[str] = None, y_column: Optional[str] = None,
                 title: str = "数据图表", save_path: Optional[str] = None) -> str:
    """根据数据创建图表。

    Args:
        file_path: 数据文件的绝对路径（CSV/Excel）
        chart_type: 图表类型 - bar(柱状图)、line(折线图)、pie(饼图)、scatter(散点图)、hist(直方图)
        x_column: X 轴列名
        y_column: Y 轴列名
        title: 图表标题
        save_path: 图表保存路径（可选，默认保存到临时目录）
    """
    import pandas as pd
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端

    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        # 读取数据
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.csv':
            df = pd.read_csv(file_path)
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(file_path)
        else:
            return f"错误: 不支持的文件类型 {ext}"

        # 创建图表
        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "bar":
            if not x_column or not y_column:
                return "错误: 柱状图需要 x_column 和 y_column"
            ax.bar(df[x_column], df[y_column])
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)

        elif chart_type == "line":
            if not x_column or not y_column:
                return "错误: 折线图需要 x_column 和 y_column"
            ax.plot(df[x_column], df[y_column])
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)

        elif chart_type == "pie":
            if not x_column:
                return "错误: 饼图需要 x_column"
            ax.pie(df[x_column], labels=df[x_column].index if y_column is None else df[y_column],
                   autopct='%1.1f%%')

        elif chart_type == "scatter":
            if not x_column or not y_column:
                return "错误: 散点图需要 x_column 和 y_column"
            ax.scatter(df[x_column], df[y_column])
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)

        elif chart_type == "hist":
            if not x_column:
                return "错误: 直方图需要 x_column"
            ax.hist(df[x_column], bins=20)
            ax.set_xlabel(x_column)
            ax.set_ylabel("频数")

        else:
            return f"错误: 不支持的图表类型 '{chart_type}'"

        ax.set_title(title)
        plt.tight_layout()

        # 保存图表
        if not save_path:
            save_path = os.path.join(tempfile.gettempdir(), f"chart_{chart_type}.png")

        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        plt.close()

        return f"图表已保存: {save_path}"

    except Exception as e:
        return f"错误: 创建图表失败 - {e}"


@tool
def transform_data(file_path: str, operation: str = "filter",
                   column: Optional[str] = None, condition: Optional[str] = None,
                   new_column: Optional[str] = None, formula: Optional[str] = None,
                   output_path: Optional[str] = None) -> str:
    """转换和处理数据。

    Args:
        file_path: 数据文件的绝对路径
        operation: 操作类型 - filter(筛选)、sort(排序)、drop_duplicates(去重)、add_column(添加列)、drop_column(删除列)
        column: 操作的列名
        condition: 筛选条件（filter 时使用），例如 ">100" 或 "== 'value'"
        new_column: 新列名（add_column 时使用）
        formula: 计算公式（add_column 时使用），例如 "col1 + col2"
        output_path: 输出文件路径（可选）
    """
    import pandas as pd

    try:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        # 读取数据
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.csv':
            df = pd.read_csv(file_path)
        elif ext in ('.xlsx', '.xls'):
            df = pd.read_excel(file_path)
        else:
            return f"错误: 不支持的文件类型 {ext}"

        original_shape = df.shape

        if operation == "filter":
            if not column or not condition:
                return "错误: 筛选需要 column 和 condition"
            if column not in df.columns:
                return f"错误: 列 '{column}' 不存在"
            # 安全的条件筛选
            df = df.query(f"`{column}` {condition}")

        elif operation == "sort":
            if not column:
                return "错误: 排序需要 column"
            if column not in df.columns:
                return f"错误: 列 '{column}' 不存在"
            ascending = not (condition and condition.lower() == "desc")
            df = df.sort_values(by=column, ascending=ascending)

        elif operation == "drop_duplicates":
            df = df.drop_duplicates()

        elif operation == "add_column":
            if not new_column or not formula:
                return "错误: 添加列需要 new_column 和 formula"
            # 安全的公式计算
            df[new_column] = df.eval(formula)

        elif operation == "drop_column":
            if not column:
                return "错误: 删除列需要 column"
            if column not in df.columns:
                return f"错误: 列 '{column}' 不存在"
            df = df.drop(columns=[column])

        else:
            return f"错误: 不支持的操作 '{operation}'"

        # 保存结果
        if not output_path:
            output_path = file_path

        if output_path.endswith('.csv'):
            df.to_csv(output_path, index=False)
        else:
            df.to_excel(output_path, index=False)

        return (f"数据转换完成\n"
                f"原始形状: {original_shape}\n"
                f"处理后形状: {df.shape}\n"
                f"已保存到: {output_path}")

    except Exception as e:
        return f"错误: 数据转换失败 - {e}"
