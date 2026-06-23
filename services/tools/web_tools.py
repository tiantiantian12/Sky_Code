"""
网络访问工具集
为 Agent 提供 HTTP 请求、网页抓取、API 调用能力
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
