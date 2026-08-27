"""
网络访问工具集
为 Agent 提供 HTTP 请求、网页抓取、API 调用、网络搜索能力
"""

import json
import os
import tempfile
from typing import Optional
from langchain_core.tools import tool


@tool
def http_request(url: str, method: str = "GET", headers: Optional[str] = None,
                 body: Optional[str] = None, timeout: int = 30) -> str:
    """发送 HTTP 请求。支持 GET/POST/PUT/DELETE 等方法。

    Args:
        url: 请求的 URL 地址，例如 "https://api.example.com/data"
        method: HTTP 方法，如 GET、POST、PUT、DELETE（默认 GET）
        headers: 请求头 JSON 字符串，例如 '{"Content-Type": "application/json"}'
        body: 请求体内容（POST/PUT 时使用）
        timeout: 请求超时时间（秒，默认 30）
    """
    import requests

    try:
        # 解析 headers
        parsed_headers = {}
        if headers:
            try:
                parsed_headers = json.loads(headers)
            except json.JSONDecodeError:
                return "错误: headers 格式无效，需要 JSON 格式"

        # 发送请求
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=parsed_headers,
            data=body,
            timeout=timeout
        )

        # 构建响应信息
        result_parts = [
            f"状态码: {response.status_code}",
            f"响应头: {dict(response.headers)}",
            ""
        ]

        # 尝试解析响应体
        content_type = response.headers.get('content-type', '')
        if 'json' in content_type:
            try:
                json_data = response.json()
                result_parts.append(f"响应体 (JSON):\n{json.dumps(json_data, ensure_ascii=False, indent=2)}")
            except Exception:
                result_parts.append(f"响应体:\n{response.text[:5000]}")
        elif 'text' in content_type or 'html' in content_type or 'xml' in content_type:
            result_parts.append(f"响应体:\n{response.text[:5000]}")
        else:
            result_parts.append(f"响应体 (二进制, {len(response.content)} bytes)")

        return "\n".join(result_parts)

    except requests.exceptions.Timeout:
        return f"错误: 请求超时 ({timeout}秒)"
    except requests.exceptions.ConnectionError:
        return f"错误: 连接失败 - {url}"
    except requests.exceptions.RequestException as e:
        return f"错误: 请求失败 - {e}"
    except Exception as e:
        return f"错误: {e}"


@tool
def fetch_webpage(url: str, selector: Optional[str] = None, extract_text: bool = True) -> str:
    """抓取网页内容。可以提取整个页面或特定元素的文本。

    Args:
        url: 网页 URL 地址
        selector: CSS 选择器（可选），用于提取特定元素，例如 "#content" 或 ".article"
        extract_text: 是否只提取文本（默认 True），False 则返回 HTML
    """
    import requests
    from bs4 import BeautifulSoup

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # 设置正确的编码
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        # 移除 script 和 style 标签
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()

        if selector:
            elements = soup.select(selector)
            if not elements:
                return f"未找到匹配 '{selector}' 的元素"
            
            results = []
            for i, elem in enumerate(elements[:10]):  # 最多返回 10 个元素
                if extract_text:
                    text = elem.get_text(separator='\n', strip=True)
                    results.append(f"[元素 {i+1}]\n{text}")
                else:
                    results.append(f"[元素 {i+1}]\n{str(elem)[:2000]}")
            return "\n\n".join(results)
        else:
            if extract_text:
                text = soup.get_text(separator='\n', strip=True)
                # 限制长度
                if len(text) > 10000:
                    text = text[:10000] + "\n\n... (内容已截断)"
                return text
            else:
                html = str(soup)
                if len(html) > 10000:
                    html = html[:10000] + "\n\n... (内容已截断)"
                return html

    except requests.exceptions.RequestException as e:
        return f"错误: 请求失败 - {e}"
    except Exception as e:
        return f"错误: {e}"


@tool
def api_call(url: str, method: str = "POST", headers: Optional[str] = None,
             json_body: Optional[str] = None, timeout: int = 60) -> str:
    """调用 API 接口。自动处理 JSON 请求和响应。

    Args:
        url: API 端点 URL
        method: HTTP 方法（默认 POST）
        headers: 请求头 JSON 字符串（可选）
        json_body: JSON 请求体字符串，例如 '{"key": "value"}'
        timeout: 请求超时时间（秒，默认 60）
    """
    import requests

    try:
        # 解析 headers
        parsed_headers = {'Content-Type': 'application/json'}
        if headers:
            try:
                parsed_headers.update(json.loads(headers))
            except json.JSONDecodeError:
                return "错误: headers 格式无效，需要 JSON 格式"

        # 解析 JSON body
        parsed_body = None
        if json_body:
            try:
                parsed_body = json.loads(json_body)
            except json.JSONDecodeError:
                return "错误: json_body 格式无效，需要 JSON 格式"

        # 发送请求
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=parsed_headers,
            json=parsed_body,
            timeout=timeout
        )

        # 构建响应信息
        result = {
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300
        }

        # 尝试解析 JSON 响应
        try:
            result["data"] = response.json()
        except Exception:
            result["data"] = response.text[:5000]

        return json.dumps(result, ensure_ascii=False, indent=2)

    except requests.exceptions.Timeout:
        return f"错误: 请求超时 ({timeout}秒)"
    except requests.exceptions.ConnectionError:
        return f"错误: 连接失败 - {url}"
    except requests.exceptions.RequestException as e:
        return f"错误: 请求失败 - {e}"
    except Exception as e:
        return f"错误: {e}"


@tool
def download_file(url: str, save_path: Optional[str] = None, filename: Optional[str] = None) -> str:
    """下载文件到本地。

    Args:
        url: 文件下载地址
        save_path: 保存目录（可选，默认保存到临时目录）
        filename: 文件名（可选，默认从 URL 或 Content-Disposition 提取）
    """
    import requests

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        response.raise_for_status()

        # 获取文件名
        if not filename:
            # 从 Content-Disposition 获取
            cd = response.headers.get('content-disposition', '')
            if 'filename=' in cd:
                filename = cd.split('filename=')[-1].strip('"\'')
            else:
                # 从 URL 获取
                filename = url.split('/')[-1].split('?')[0]
                if not filename:
                    filename = 'download'

        # 确定保存路径
        if not save_path:
            save_path = tempfile.gettempdir()

        full_path = os.path.join(save_path, filename)

        # 下载文件
        with open(full_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        file_size = os.path.getsize(full_path)
        return f"下载成功: {full_path}\n文件大小: {file_size} bytes"

    except requests.exceptions.RequestException as e:
        return f"错误: 下载失败 - {e}"
    except Exception as e:
        return f"错误: {e}"


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """网络搜索。当你需要查找实时信息、不确定的知识、最新新闻、技术文档等时使用。
    搜索关键词应简洁明确，例如 "Python 3.12 新特性" 而非整句话。

    Args:
        query: 搜索关键词，例如 "2025年诺贝尔物理学奖得主"
        max_results: 返回的最大结果数（默认 5，最多 10）
    """
    if max_results > 10:
        max_results = 10

    errors = []

    # 引擎1: ddgs（duckduckgo_search 新版，后端兼容性更好）
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if results:
            return _format_search_results(query, results)
        errors.append("ddgs: 无结果返回")
    except ImportError:
        errors.append("ddgs 未安装")
    except Exception as e:
        errors.append(f"ddgs: {e}")

    # 引擎2: duckduckgo_search（旧版兼容）
    try:
        from duckduckgo_search import DDGS as OldDDGS
        with OldDDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if results:
            return _format_search_results(query, results)
        errors.append("duckduckgo_search: 无结果返回")
    except ImportError:
        errors.append("duckduckgo-search 未安装")
    except Exception as e:
        errors.append(f"duckduckgo_search: {e}")

    # 引擎3: cn.bing.com 直接抓取（国内可用，requests + BeautifulSoup）
    try:
        results = _scrape_cn_bing(query, max_results)
        if results:
            return _format_search_results(query, results)
        errors.append("cn.bing.com: 无结果返回")
    except ImportError:
        errors.append("cn.bing.com: 缺少 beautifulsoup4 库")
    except Exception as e:
        errors.append(f"cn.bing.com: {e}")

    # 所有引擎都失败
    return (
        f"搜索失败：所有搜索引擎均不可用。\n"
        f"详情：{'；'.join(errors)}\n"
        f"建议：请检查网络连接，或尝试使用 http_request 工具直接访问搜索引擎。"
    )


def _format_search_results(query: str, results: list) -> str:
    """格式化搜索结果"""
    lines = [f"搜索关键词: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        href = r.get("href", "无链接")
        body = r.get("body", "无描述")
        lines.append(f"[{i}] {title}")
        lines.append(f"    URL: {href}")
        lines.append(f"    摘要: {body}")
        lines.append("")
    return "\n".join(lines)


def _scrape_cn_bing(query: str, max_results: int = 5) -> list:
    """从 cn.bing.com 抓取搜索结果（国内网络可用）"""
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    resp = requests.get(
        "https://cn.bing.com/search",
        params={"q": query, "count": max_results},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for item in soup.select("li.b_algo")[:max_results]:
        title_el = item.select_one("h2 a")
        desc_el = item.select_one(".b_caption p, .b_lineclamp2, .b_algoSlug")
        if not title_el:
            continue
        results.append({
            "title": title_el.get_text(strip=True),
            "href": title_el.get("href", ""),
            "body": desc_el.get_text(strip=True) if desc_el else "",
        })
    return results
