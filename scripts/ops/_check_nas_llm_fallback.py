#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 NAS 上 LAAP 的 LLM 兜底是否正常"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)

# 1. NAS .env 的 LLM 配置
cmd1 = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && grep -E 'DEEPSEEK_API_KEY|DEEPSEEK_BASE_URL|LLM_MODEL' .env 2>/dev/null | sed 's/\\(DEEPSEEK_API_KEY=\\).*/\\1***/;s/\\(DEEPSEEK_BASE_URL=\\).*/\\1***/' """
_, out, _ = c.exec_command(cmd1, timeout=20)
print("=== NAS .env LLM 配置 ===")
print(out.read().decode().strip() or "(无匹配)")

# 2. 直接测 LLM 兜底
cmd2 = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && ./.venv/bin/python -c "
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
import laap_brain.api as api
key = api._llm_api_key()
print('key:', 'OK(' + str(len(key)) + ')' if key else 'MISSING')
print('base_url:', os.environ.get('DEEPSEEK_BASE_URL', 'default'))
print('model:', os.environ.get('LLM_MODEL', 'deepseek-chat'))
try:
    r = api._llm_tail_fallback('你好，你是谁？')
    if r:
        print('兜底OK:', r.get('content', '')[:60])
    else:
        print('兜底None')
except Exception as e:
    print('兜底异常:', type(e).__name__, str(e)[:150])
" 2>&1 | grep -vE 'tokenizers|TF-IDF|transformers'"""
_, out, _ = c.exec_command(cmd2, timeout=90)
print("\n=== NAS LLM 兜底实测 ===")
print(out.read().decode().strip()[:600])
c.close()
