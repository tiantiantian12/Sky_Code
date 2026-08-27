"""测试文件列表和代码验证区域的 bug 修复"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

print("Test 1: FileChangesPanel.add_file path normalization...")
try:
    from ui.widgets import FileChangesPanel
    panel = FileChangesPanel()
    # 模拟 mark_file_editing 添加文件（使用规范化路径）
    panel.mark_file_editing("d:\\test\\file.py", "write_file")
    # 模拟 add_file 添加同一文件（使用原始路径）
    panel.add_file("修改", "d:\\test\\file.py", added=10, removed=5)
    # 应该只有一条记录
    assert len(panel._files) == 1, f"Expected 1 entry, got {len(panel._files)}: {list(panel._files.keys())}"
    # 该记录应该有 added=10, removed=5
    key = list(panel._files.keys())[0]
    entry = panel._files[key]
    assert entry["added"] == 10, f"Expected added=10, got {entry['added']}"
    assert entry["removed"] == 5, f"Expected removed=5, got {entry['removed']}"
    assert entry["editing"] == False, f"Expected editing=False, got {entry['editing']}"
    print("OK - No duplicate entries, stats merged correctly")
except Exception as e:
    print(f"FAIL - {e}")

print("\nTest 2: CodeFeedbackWidget.resolve_all_pending...")
try:
    from ui.widgets import CodeFeedbackWidget
    w = CodeFeedbackWidget()
    w.add_file_editing("d:\\test\\file1.py", "write_file")
    w.add_file_editing("d:\\test\\file2.py", "edit_file")
    assert len(w._pending_file_edits) == 2, f"Expected 2 pending, got {len(w._pending_file_edits)}"
    # 兜底清理
    w.resolve_all_pending()
    assert len(w._pending_file_edits) == 0, f"Expected 0 pending after resolve_all, got {len(w._pending_file_edits)}"
    assert w._spinner_timer is None, "Spinner timer should be None after resolve_all"
    print("OK - All pending entries cleared, spinner stopped")
except Exception as e:
    print(f"FAIL - {e}")

print("\nTest 3: FileChangesPanel.resolve_all_pending...")
try:
    from ui.widgets import FileChangesPanel
    panel = FileChangesPanel()
    panel.mark_file_editing("d:\\test\\file1.py", "write_file")
    panel.mark_file_editing("d:\\test\\file2.py", "edit_file")
    assert len(panel._pending_files) == 2, f"Expected 2 pending, got {len(panel._pending_files)}"
    # 兜底清理
    panel.resolve_all_pending()
    assert len(panel._pending_files) == 0, f"Expected 0 pending after resolve_all, got {len(panel._pending_files)}"
    assert panel._spinner_timer is None, "Spinner timer should be None after resolve_all"
    # 文件记录应该还在，但 editing 标志应该为 False
    for key, info in panel._files.items():
        assert info["editing"] == False, f"File {key} still has editing=True"
    print("OK - All pending entries cleared, spinner stopped")
except Exception as e:
    print(f"FAIL - {e}")

print("\nTest 4: CodeFeedbackWidget.resolve_file_editing (public method)...")
try:
    from ui.widgets import CodeFeedbackWidget
    w = CodeFeedbackWidget()
    w.add_file_editing("d:\\test\\file.py", "write_file")
    assert len(w._pending_file_edits) == 1
    # 使用公开方法停止转圈
    w.resolve_file_editing("d:\\test\\file.py")
    assert len(w._pending_file_edits) == 0, f"Expected 0 pending, got {len(w._pending_file_edits)}"
    print("OK - resolve_file_editing public method works")
except Exception as e:
    print(f"FAIL - {e}")

print("\n" + "="*50)
print("All tests completed!")