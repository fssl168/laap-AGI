#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 NAS sentence-transformers（不过滤输出）"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
cmd = """cd /vol1/@appdata/trim.hermes/workspace/laap-AGI && unset PYTHONPATH && ./.venv/bin/python -c "
import warnings; warnings.filterwarnings('ignore')
import sys; sys.path.insert(0, '.')
from aris_brain.laap_semantic_memory import _get_embedding_provider
p = _get_embedding_provider()
print('PROVIDER:', type(p).__name__)
emb = p.embed(['测试嵌入'])
print('EMB_DIM:', emb.shape if hasattr(emb, 'shape') else len(emb))
" 2>&1 | tail -3"""
_, out, _ = c.exec_command(cmd, timeout=300)
print(out.read().decode().strip())
c.close()
