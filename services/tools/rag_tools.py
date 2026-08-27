"""
RAG (Retrieval-Augmented Generation) 工具模块
使用向量数据库实现代码检索功能
"""

import os
import time
import hashlib
import threading
from typing import List, Dict, Optional
from langchain_core.tools import tool

# 向量数据库和 Embedding 模型
_chroma_client = None
_collection = None
_embedding_model = None
_embedding_loading = False  # 防止并发重复加载
_indexed_files = {}  # 记录已索引文件的 hash

# 支持的代码文件扩展名
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h',
    '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt',
    '.html', '.css', '.scss', '.less', '.json', '.xml', '.yaml', '.yml',
    '.toml', '.ini', '.cfg', '.conf', '.md', '.txt', '.sh', '.bat'
}


# ── Hugging Face 镜像配置 ──
_HF_MIRROR = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")


def _get_embedding_model():
    """获取 Embedding 模型（懒加载）
    优先使用本地缓存，否则从镜像下载，均超时 60 秒自动降级
    """
    global _embedding_model, _embedding_loading

    # 已加载成功，直接返回
    if _embedding_model is not None:
        return _embedding_model

    # 正在加载中或已加载失败过，不再重试
    if _embedding_loading:
        return None

    _embedding_loading = True

    def _load_model():
        import os as _os
        _os.environ["HF_ENDPOINT"] = _HF_MIRROR
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(
                'paraphrase-multilingual-MiniLM-L12-v2',
                device='cpu',
            )
            return model
        except Exception as e:
            # 镜像也失败了，最后尝试本地文件
            try:
                _os.environ["HF_HUB_OFFLINE"] = "1"
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(
                    'paraphrase-multilingual-MiniLM-L12-v2',
                    device='cpu',
                    local_files_only=True,
                )
                return model
            except Exception:
                raise e  # 重新抛出原始错误

    try:
        result_container = {"model": None}
        t = threading.Thread(target=lambda: result_container.update({"model": _load_model()}), daemon=True)
        t.start()
        t.join(timeout=60)  # 最多等 60 秒
        if t.is_alive():
            print("Embedding 模型加载超时（60s），RAG 功能将不可用。可设置 HF_ENDPOINT 环境变量使用镜像。")
            return None
        _embedding_model = result_container["model"]
        if _embedding_model is not None:
            print("Embedding 模型加载成功")
            return _embedding_model
        else:
            print("Embedding 模型加载失败，RAG 功能将不可用")
            return None
    except Exception as e:
        print(f"Embedding 模型加载失败: {e}")
        return None


def _get_collection():
    """获取 ChromaDB 集合（懒加载）"""
    global _chroma_client, _collection
    if _collection is None:
        try:
            import chromadb
            from chromadb.config import Settings
            # 使用持久化存储 - 项目根目录下的 data/chromadb
            # __file__ = services/tools/rag_tools.py
            # 需要往上3级到项目根目录 LLM_Agent
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            db_path = os.path.join(project_root, 'data', 'chromadb')
            os.makedirs(db_path, exist_ok=True)
            print(f"ChromaDB 路径: {db_path}")
            _chroma_client = chromadb.PersistentClient(path=db_path)
            _collection = _chroma_client.get_or_create_collection(
                name="code_snippets",
                metadata={"hnsw:space": "cosine"}
            )
            print(f"ChromaDB 集合加载成功，已有 {_collection.count()} 条记录")
        except Exception as e:
            print(f"ChromaDB 加载失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    return _collection


def _get_file_hash(file_path: str) -> str:
    """获取文件内容的 hash"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return hashlib.md5(content.encode()).hexdigest()
    except Exception:
        return ""


def _split_code_into_chunks(content: str, file_path: str, chunk_size: int = 500, overlap: int = 50) -> List[Dict]:
    """将代码分割成块"""
    lines = content.split('\n')
    chunks = []
    
    # 按函数/类分割
    current_chunk = []
    current_start = 1
    current_func = ""
    
    for i, line in enumerate(lines, 1):
        # 检测函数/类定义
        if line.strip().startswith(('def ', 'class ', 'function ', 'async def ')):
            if current_chunk:
                chunks.append({
                    'content': '\n'.join(current_chunk),
                    'file_path': file_path,
                    'start_line': current_start,
                    'end_line': i - 1,
                    'function': current_func
                })
            current_chunk = [line]
            current_start = i
            current_func = line.strip()
        else:
            current_chunk.append(line)
            
            # 如果块太大，分割
            if len('\n'.join(current_chunk)) > chunk_size * 4:  # 大约 4 个字符 = 1 个 token
                chunks.append({
                    'content': '\n'.join(current_chunk),
                    'file_path': file_path,
                    'start_line': current_start,
                    'end_line': i,
                    'function': current_func
                })
                current_chunk = []
                current_start = i + 1
    
    # 添加最后一块
    if current_chunk:
        chunks.append({
            'content': '\n'.join(current_chunk),
            'file_path': file_path,
            'start_line': current_start,
            'end_line': len(lines),
            'function': current_func
        })
    
    return chunks


def index_file(file_path: str) -> str:
    """索引单个文件到向量数据库"""
    try:
        # 检查文件是否需要更新
        current_hash = _get_file_hash(file_path)
        if file_path in _indexed_files and _indexed_files[file_path] == current_hash:
            return f"skip: 文件 {os.path.basename(file_path)} 已是最新，跳过索引"
        
        # 读取文件内容
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        # 分割成块
        chunks = _split_code_into_chunks(content, file_path)
        
        if not chunks:
            return f"skip: 文件 {os.path.basename(file_path)} 没有可索引的内容"
        
        # 获取 Embedding 模型和集合
        embedding_model = _get_embedding_model()
        collection = _get_collection()
        
        if not embedding_model or not collection:
            return f"skip: 向量数据库或 Embedding 模型未初始化"
        
        # 删除该文件的旧索引
        try:
            collection.delete(where={"file_path": file_path})
        except Exception:
            pass
        
        # 索引每个块
        ids = []
        documents = []
        metadatas = []
        embeddings = []
        
        for i, chunk in enumerate(chunks):
            doc_id = f"{file_path}:{chunk['start_line']}:{chunk['end_line']}"
            ids.append(doc_id)
            documents.append(chunk['content'])
            metadatas.append({
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'start_line': chunk['start_line'],
                'end_line': chunk['end_line'],
                'function': chunk.get('function', '')
            })
        
        # 批量生成 Embedding
        embeddings = embedding_model.encode(documents).tolist()
        
        # 添加到集合
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings
        )
        
        # 更新索引记录
        _indexed_files[file_path] = current_hash
        
        return f"ok: 成功索引文件 {os.path.basename(file_path)}，共 {len(chunks)} 个代码块"
        
    except Exception as e:
        return f"error: 索引文件失败: {e}"


def remove_file_from_index(file_path: str) -> str:
    """从向量数据库中移除指定文件的索引（文件删除时调用）"""
    try:
        collection = _get_collection()
        if not collection:
            return "skip: 向量数据库未初始化"
        # 删除该文件的所有索引块
        try:
            collection.delete(where={"file_path": file_path})
        except Exception:
            pass
        # 同时尝试规范化路径删除（兼容旧索引）
        norm_path = os.path.normpath(file_path).replace('\\', '/')
        if norm_path != file_path:
            try:
                collection.delete(where={"file_path": norm_path})
            except Exception:
                pass
        # 从内存索引记录中移除
        _indexed_files.pop(file_path, None)
        _indexed_files.pop(norm_path, None)
        return f"ok: 已从向量数据库移除 {os.path.basename(file_path)}"
    except Exception as e:
        return f"error: 移除索引失败: {e}"


def index_directory(dir_path: str, recursive: bool = True) -> str:
    """索引目录中的所有代码文件"""
    try:
        indexed_count = 0
        skipped_count = 0
        error_count = 0
        
        # 预检查：embedding 模型是否可用
        embedding_model = _get_embedding_model()
        collection = _get_collection()
        if not embedding_model or not collection:
            return (f"索引跳过: 向量数据库或 Embedding 模型未初始化。"
                    f"请检查 sentence-transformers 和 chromadb 是否已安装。")
        
        for root, dirs, files in os.walk(dir_path):
            # 跳过隐藏目录和缓存目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules']
            
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in CODE_EXTENSIONS:
                    file_path = os.path.join(root, file)
                    result = index_file(file_path)
                    if result.startswith("ok:"):
                        indexed_count += 1
                    elif result.startswith("skip:"):
                        skipped_count += 1
                    else:
                        error_count += 1
            
            if not recursive:
                break
        
        return f"索引完成: {indexed_count} 个文件已索引, {skipped_count} 个跳过, {error_count} 个错误"
        
    except Exception as e:
        return f"索引目录失败: {e}"


@tool
def search_code(query: str, top_k: int = 5) -> str:
    """在已索引的代码库中搜索相关内容。用于查找特定功能、函数或代码片段。
    
    Args:
        query: 搜索查询，例如 "用户登录" 或 "数据库连接"
        top_k: 返回的结果数量，默认 5
    """
    try:
        collection = _get_collection()
        embedding_model = _get_embedding_model()
        
        if not collection or not embedding_model:
            return "向量数据库或 Embedding 模型未初始化，请先运行 index_directory 索引代码"
        
        if collection.count() == 0:
            return "向量数据库为空，请先运行 index_directory 索引代码目录"
        
        # 生成查询的 Embedding
        query_embedding = embedding_model.encode([query]).tolist()[0]
        
        # 搜索
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count())
        )
        
        if not results['documents'][0]:
            return "没有找到相关代码"
        
        # 格式化结果
        output = []
        for i, (doc, metadata) in enumerate(zip(results['documents'][0], results['metadatas'][0])):
            file_name = metadata.get('file_name', 'unknown')
            start_line = metadata.get('start_line', 0)
            end_line = metadata.get('end_line', 0)
            function = metadata.get('function', '')
            
            header = f"--- 结果 {i+1}: {file_name} (行 {start_line}-{end_line})"
            if function:
                header += f" [{function}]"
            header += " ---"
            
            output.append(header)
            output.append(doc[:500])  # 限制显示长度
            output.append("")
        
        return "\n".join(output)
        
    except Exception as e:
        return f"搜索失败: {e}"


@tool
def get_index_status() -> str:
    """获取向量数据库的索引状态，包括已索引的文件数量和集合大小。"""
    try:
        collection = _get_collection()
        
        if not collection:
            return "向量数据库未初始化"
        
        count = collection.count()
        indexed_files = len(_indexed_files)
        
        return f"向量数据库状态:\n- 集合中的代码块数量: {count}\n- 已索引的文件数量: {indexed_files}"
        
    except Exception as e:
        return f"获取状态失败: {e}"


def set_rollback_manager(mgr):
    """设置回滚管理器（RAG 工具不需要）"""
    pass


# ── 后台自动索引 ─────────────────────────────────────────────
import threading as _threading

_auto_index_lock = _threading.Lock()
_auto_index_running: dict = {}  # {dir_path: bool} 标记正在索引的目录
_auto_index_callback: callable = None  # 索引完成后的回调（UI 更新用）


def set_auto_index_callback(callback: callable):
    """设置索引完成回调，用于 UI 状态更新。回调签名为 callback(dir_path: str, result: str)"""
    global _auto_index_callback
    _auto_index_callback = callback


def _auto_index_thread(dir_path: str):
    """后台线程：索引目录到向量数据库"""
    global _auto_index_running
    try:
        result = index_directory(dir_path)
        print(f"[RAG 后台索引] {result}")
        if _auto_index_callback:
            try:
                _auto_index_callback(dir_path, result)
            except Exception:
                pass
    except Exception as e:
        print(f"[RAG 后台索引] 异常: {e}")
    finally:
        with _auto_index_lock:
            _auto_index_running.pop(os.path.normpath(dir_path).lower(), None)


def auto_index_workspace(dir_path: str, delay_seconds: float = 1.0):
    """自动在后台索引工作区（非阻塞）。
    使用 debounce 延迟避免频繁重索引；同一目录如果正在索引中则跳过。

    Args:
        dir_path: 工作区目录路径
        delay_seconds: 延迟秒数（默认 1.0），用于 debounce
    """
    if not dir_path or not os.path.isdir(dir_path):
        return
    norm = os.path.normpath(dir_path).lower()
    with _auto_index_lock:
        if norm in _auto_index_running:
            return  # 已在索引中，跳过
        _auto_index_running[norm] = True

    def _delayed_index():
        time.sleep(delay_seconds)  # debounce
        # 再次确认目录仍存在
        if not os.path.isdir(dir_path):
            with _auto_index_lock:
                _auto_index_running.pop(norm, None)
            return
        _auto_index_thread(dir_path)

    t = _threading.Thread(target=_delayed_index, daemon=True)
    t.start()


def warm_rag_and_scan(workspace_path: str):
    """同时预热 scan_project 缓存 和 RAG 向量索引（非阻塞）。
    在打开工作区时调用，确保后续 Agent 对话可以直接使用缓存数据。

    Args:
        workspace_path: 工作区绝对路径
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return

    # 1. 预热 scan_project 缓存
    try:
        from services.tools.file_tools import warm_scan_cache
        warm_scan_cache(workspace_path)
    except Exception:
        pass

    # 2. 后台索引 RAG 向量数据库（带 debounce）
    auto_index_workspace(workspace_path)


def get_rag_status_for_prompt(dir_path: str = None) -> str:
    """获取 RAG 索引状态的简短描述，用于注入 system prompt。
    返回空字符串表示无索引。
    """
    try:
        collection = _get_collection()
        if not collection:
            return ""
        count = collection.count()
        if count == 0:
            return ""
        indexed_count = len(_indexed_files)
        return (
            f"向量数据库已就绪（{indexed_count} 个文件，{count} 个代码块）；"
            f"可通过 search_code 进行语义检索。"
        )
    except Exception:
        return ""
