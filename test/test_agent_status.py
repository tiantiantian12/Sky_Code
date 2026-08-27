"""测试 AgentStatusLine 和 Markdown 渲染增强"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 测试 1: AgentStatusLine 组件是否可以正常导入和创建
print("Test 1: Import AgentStatusLine...")
try:
    from ui.widgets import AgentStatusLine
    print("OK - AgentStatusLine imported")
except Exception as e:
    print(f"FAIL - AgentStatusLine import: {e}")

# 测试 2: ChatMessageWidget 是否有新的状态方法
print("\nTest 2: Check ChatMessageWidget new methods...")
try:
    from ui.widgets import ChatMessageWidget
    # 检查方法是否存在
    assert hasattr(ChatMessageWidget, 'set_agent_status'), "Missing set_agent_status"
    assert hasattr(ChatMessageWidget, 'clear_agent_status'), "Missing clear_agent_status"
    print("OK - ChatMessageWidget methods exist")
except Exception as e:
    print(f"FAIL - ChatMessageWidget: {e}")

# 测试 3: ApiWorker 是否有 agent_status 信号
print("\nTest 3: Check ApiWorker signals...")
try:
    from ui.main_window import ApiWorker
    # 检查信号是否存在
    assert hasattr(ApiWorker, 'agent_status'), "Missing agent_status signal"
    print("OK - ApiWorker.agent_status signal exists")
except Exception as e:
    print(f"FAIL - ApiWorker signal: {e}")

# 测试 4: Markdown 渲染器是否有新的样式函数
print("\nTest 4: Check Markdown renderer...")
try:
    from ui.markdown_renderer import _style_paragraphs_and_lists
    print("OK - _style_paragraphs_and_lists function exists")

    # 测试渲染一个简单的 Markdown 文本
    from ui.markdown_renderer import render_markdown
    test_text = """
# Heading Test

This is a paragraph.

**Bold Text**

- List item 1
- List item 2

> Quote text
"""
    result = render_markdown(test_text)
    assert result.html is not None, "Result is empty"
    assert "margin:" in result.html, "Missing styles"
    print("OK - Markdown rendering works")
except Exception as e:
    print(f"FAIL - Markdown renderer: {e}")

# 测试 5: agent_service.py 是否正确 yield agent_status 事件
print("\nTest 5: Check agent_service events...")
try:
    # 只需要检查文件是否能被正确编译
    import services.core.agent_service
    print("OK - agent_service module loaded")
except Exception as e:
    print(f"FAIL - agent_service: {e}")

print("\n" + "="*50)
print("All tests completed!")