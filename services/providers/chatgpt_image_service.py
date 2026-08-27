"""ChatGPT 网页版 Image 2 出图 — 复用 Chrome 调试端口，无需 OpenAI API 额度"""

import os
import re
import time
import base64
import urllib.request
from typing import Optional, Callable

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "data", "generated")

CHATGPT_IMAGE2_MODEL_ID = "chatgpt-image2"


def _notify(cb: Optional[Callable[[str], None]], msg: str):
    if cb and msg:
        try:
            cb(msg)
        except Exception:
            pass


def _ensure_generated_dir():
    os.makedirs(GENERATED_DIR, exist_ok=True)


def _select_image_mode(driver, status_callback=None) -> bool:
    """在 ChatGPT 网页中选择 Image / Image 2 出图模式"""
    _notify(status_callback, "正在切换到 Image 2 出图模式...")
    try:
        selected = driver.execute_script(
            """
            var keywords = [
              'Image 2', 'Images 2', 'GPT Image 2', 'GPT Image',
              'Create image', 'Image generation', 'Images',
              '图片', '图像', '出图', 'Image'
            ];
            function visible(el) {
              if (!el) return false;
              var r = el.getBoundingClientRect();
              return r.width > 0 && r.height > 0;
            }
            function clickEl(el) {
              if (!el) return false;
              el.click();
              return true;
            }
            function textOf(el) {
              return (el.innerText || el.textContent || '').trim();
            }
            // 1) 打开模型/工具选择器
            var openSelectors = [
              'button[data-testid="model-switcher-dropdown-button"]',
              'button[data-testid="composer-model-selector"]',
              'button[aria-label*="Model"]',
              'button[aria-label*="模型"]',
              'button[aria-haspopup="menu"]',
              '[data-testid="composer-action-model-selector"]'
            ];
            for (var i = 0; i < openSelectors.length; i++) {
              var btn = document.querySelector(openSelectors[i]);
              if (visible(btn)) { clickEl(btn); break; }
            }
            // 2) 在菜单/页面中点击 Image 相关项
            var candidates = document.querySelectorAll(
              'button, a, div[role="menuitem"], div[role="option"], li, span'
            );
            for (var k = 0; k < keywords.length; k++) {
              var kw = keywords[k];
              for (var j = 0; j < candidates.length; j++) {
                var node = candidates[j];
                if (!visible(node)) continue;
                var t = textOf(node);
                if (!t || t.length > 80) continue;
                if (t.toLowerCase().indexOf(kw.toLowerCase()) >= 0) {
                  clickEl(node);
                  return true;
                }
              }
            }
            // 3) 侧边栏 Images 入口
            var links = document.querySelectorAll('a[href*="image"], nav a, aside a');
            for (var n = 0; n < links.length; n++) {
              var lt = textOf(links[n]).toLowerCase();
              if (lt.indexOf('image') >= 0 || lt.indexOf('图片') >= 0 || lt.indexOf('图像') >= 0) {
                clickEl(links[n]);
                return true;
              }
            }
            return false;
            """
        )
        time.sleep(1.5)
        if selected:
            _notify(status_callback, "已切换到 Image 出图模式。")
            return True
        _notify(status_callback, "未找到 Image 模式按钮，将直接发送出图提示词。")
        return False
    except Exception as e:
        _notify(status_callback, f"切换 Image 模式时: {e}")
        return False


def _upload_reference_image(driver, image_path: str, status_callback=None) -> bool:
    _notify(status_callback, "正在上传参考图...")
    try:
        abs_path = os.path.abspath(image_path)
        inputs = driver.find_elements("css selector", 'input[type="file"]')
        for inp in inputs:
            try:
                inp.send_keys(abs_path)
                time.sleep(1.5)
                _notify(status_callback, "参考图已上传。")
                return True
            except Exception:
                continue
        clicked = driver.execute_script(
            """
            var btns = document.querySelectorAll('button,[role="button"]');
            for (var i = 0; i < btns.length; i++) {
              var t = (btns[i].innerText || btns[i].getAttribute('aria-label') || '').toLowerCase();
              if (t.indexOf('attach') >= 0 || t.indexOf('upload') >= 0 || t.indexOf('上传') >= 0) {
                btns[i].click(); return true;
              }
            }
            return false;
            """
        )
        if clicked:
            time.sleep(0.8)
            inputs = driver.find_elements("css selector", 'input[type="file"]')
            for inp in inputs:
                try:
                    inp.send_keys(abs_path)
                    time.sleep(1.5)
                    _notify(status_callback, "参考图已上传。")
                    return True
                except Exception:
                    continue
    except Exception as e:
        _notify(status_callback, f"参考图上传失败: {e}")
    return False


def _poll_image_state(driver):
    """轮询当前页面图片生成状态（兼容多版本 ChatGPT DOM）"""
    try:
        return driver.execute_script(
            """
            // 1. 检测是否还在生成中
            var stopBtn = document.querySelector(
              "button[data-testid='stop-button'],"
              + "button[aria-label*='Stop'],"
              + "button[aria-label*='停止']"
            );
            var loading = document.querySelector(
              "[data-testid='conversation-turn-response-loading'],"
              + "[data-stream-loading='true'],"
              + "[aria-label*='思考中'],"
              + "[aria-label*='thinking'],"
              + "[aria-label*='生成中']"
            );
            var generating = !!(stopBtn && stopBtn.offsetParent)
                          || !!(loading && loading.offsetParent);

            // 2. 多渠道查找生成的图片（兼容不同版本 ChatGPT DOM）
            var allImgs = [];
            var added = {};
            var selectors = [
              '[data-message-author-role="assistant"] img',
              '[data-message-model-slug] img',
              '[class*="prose"] img, [class*="message"] img, [class*="response"] img',
              'article img, main img'
            ];
            for (var si = 0; si < selectors.length; si++) {
              try {
                var nodes = document.querySelectorAll(selectors[si]);
                for (var ni = 0; ni < nodes.length; ni++) {
                  var img = nodes[ni];
                  var src = img.currentSrc || img.src || '';
                  if (!src || added[src]) continue;
                  var w = img.naturalWidth || img.width || 0;
                  var h = img.naturalHeight || img.height || 0;
                  if (/avatar|icon|logo|favicon|profile|placeholder/i.test(src)) continue;
                  // 至少 200x200 避免抓取 UI 小图标
                  if (w < 200 || h < 200) continue;
                  if (/^data:image\\/svg/i.test(src)) continue;
                  added[src] = true;
                  allImgs.push(src);
                }
              } catch(e) {}
            }
            return {
              generating: generating,
              imageCount: allImgs.length,
              lastImage: allImgs.length ? allImgs[allImgs.length - 1] : ''
            };
            """
        )
    except Exception:
        return {"generating": False, "imageCount": 0, "lastImage": ""}


def _wait_for_generated_image(driver, baseline_count=0, baseline_src="", timeout=300,
                              status_callback=None):
    """轮询等待图片生成完成，返回图片 src"""
    last_src = ""
    stable = 0
    saw_generating = False
    loop_max = timeout * 2  # 每 0.5s 一次
    for i in range(loop_max):
        time.sleep(0.5)
        state = _poll_image_state(driver)

        # 报告进度
        if status_callback and i % 10 == 0:
            _notify(status_callback, f"等待图片生成中... ({int(i * 0.5)}s)")

        if state.get("generating"):
            saw_generating = True
            stable = 0
            continue

        count = int(state.get("imageCount") or 0)
        src = str(state.get("lastImage") or "")

        # 尚未检测到生成活动且图片数量没变化 → 继续等待
        if count <= baseline_count and src == baseline_src and not saw_generating:
            continue
        if not src:
            # 刚结束生成，再给一点渲染时间
            if saw_generating and i < loop_max - 6:
                continue
            continue

        if src == last_src:
            stable += 1
            if stable >= 4:
                return src
        else:
            last_src = src
            stable = 0

    return last_src


def _download_image_src(driver, src: str, dest_path: str):
    """下载图片 src 到本地文件"""
    if src.startswith("blob:") or src.startswith("data:"):
        b64 = driver.execute_async_script(
            """
            var url = arguments[0];
            var cb = arguments[arguments.length-1];
            function toB64(blob){
              var reader = new FileReader();
              reader.onload = function(){ cb((reader.result||'').split(',')[1] || null); };
              reader.onerror = function(){ cb(null); };
              reader.readAsDataURL(blob);
            }
            if (url.indexOf('data:') === 0) {
              cb(url.split(',')[1] || null);
              return;
            }
            fetch(url).then(function(r){ return r.blob(); }).then(toB64).catch(function(){ cb(null); });
            """,
            src,
        )
        if not b64:
            raise RuntimeError(f"无法从浏览器下载图片（blob/data），源URL可能已过期: {src[:80]}")
        with open(dest_path, "wb") as f:
            f.write(base64.b64decode(b64))
        return

    headers = {}
    try:
        from services import chatgpt_service as cg
        for c in driver.get_cookies():
            name = c.get("name", "")
            val = c.get("value", "")
            if name and val:
                headers["Cookie"] = headers.get("Cookie", "") + f"{name}={val}; "
    except Exception:
        pass

    try:
        req = urllib.request.Request(src, headers=headers or {"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
    except Exception as e:
        raise RuntimeError(f"下载图片失败 [{type(e).__name__}]: {e} — 源URL: {src[:100]}") from e


def generate_image_browser(
    prompt: str,
    ref_image_path: Optional[str] = None,
    status_callback: Optional[Callable[[str], None]] = None,
    timeout: int = 300,
) -> str:
    """
    通过 ChatGPT Chrome 网页生成图片。

    Returns:
        本地保存的绝对路径
    """
    if not prompt or not prompt.strip():
        raise ValueError("提示词不能为空")

    from services import chatgpt_service as cg

    _ensure_generated_dir()
    driver = cg._ensure_page(status_callback=status_callback)
    cg._start_new_conversation(driver)
    time.sleep(1.5)
    _select_image_mode(driver, status_callback)

    before = _poll_image_state(driver)
    baseline_count = int(before.get("imageCount") or 0)
    baseline_src = str(before.get("lastImage") or "")

    if ref_image_path and os.path.isfile(ref_image_path):
        _upload_reference_image(driver, ref_image_path, status_callback)

    user_prompt = prompt.strip()
    if not re.search(r"(生成|创建|画|绘制|create|generate|draw|image|图片|图像)", user_prompt, re.I):
        user_prompt = f"请生成一张图片：{user_prompt}"

    _notify(status_callback, "正在向 ChatGPT 发送出图提示词...")
    result = cg._send_message(driver, user_prompt)
    if result.get("err"):
        raise RuntimeError(f"输入失败: {result['err']}")
    time.sleep(0.5)
    if not cg._click_send(driver):
        raise RuntimeError("发送失败：无法点击发送按钮")

    _notify(status_callback, "ChatGPT 正在生成图片，请稍候...")
    image_src = _wait_for_generated_image(
        driver,
        baseline_count=baseline_count,
        baseline_src=baseline_src,
        timeout=timeout,
        status_callback=status_callback,
    )
    if not image_src:
        raise RuntimeError(
            "未检测到生成的图片。请确认 ChatGPT 账号支持 Image 出图，"
            "或在浏览器中手动切换 Image 模式后重试。"
        )

    ext = ".png"
    if ".webp" in image_src.lower():
        ext = ".webp"
    elif ".jpg" in image_src.lower() or ".jpeg" in image_src.lower():
        ext = ".jpg"
    dest = os.path.join(GENERATED_DIR, f"img_{int(time.time() * 1000)}{ext}")
    _notify(status_callback, "正在保存图片...")
    _download_image_src(driver, image_src, dest)
    _notify(status_callback, f"图片已保存: {dest}")
    return dest
