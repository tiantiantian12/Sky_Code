with open(r"D:\qt_project\LLM_Agent\services\agent_service.py","r",encoding="utf-8") as f: lines=f.readlines()
for i,l in enumerate(lines):
    if "class AgentService" in l or "def __init__" in l and i > 300:
        start=max(0,i-2); end=min(len(lines),i+30)
        open(r"D:\qt_project\LLM_Agent\test\class_dump.txt","w",encoding="utf-8").write("".join(lines[start:end]))
        break
