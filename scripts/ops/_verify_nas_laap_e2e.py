# -*- coding: utf-8 -*-
"""验证 NAS LAAP 新代码生效。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMDS = [
    f"curl -s http://127.0.0.1:11546/health",
    f"""cd {NAS_ROOT} && ./.venv/bin/python -c "
import urllib.request, json
key='sk-unsloth-4257b387b9b16f798969c35ccc099db6'
payload={{'model':'laap-core','messages':[{{'role':'user','content':'跑回测'}}]}}
req=urllib.request.Request('http://127.0.0.1:11546/v1/chat/completions',
  data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
  headers={{'Content-Type':'application/json','Authorization':'Bearer '+key}})
with urllib.request.urlopen(req, timeout=90) as r:
  d=json.loads(r.read().decode('utf-8'))
print('engine:', d.get('engine'))
print('reply:', (d.get('choices',[{{}}])[0].get('message',{{}}).get('content','') or '')[:400])
" 2>&1""",
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
for cmd in CMDS:
    _, out, err = c.exec_command(cmd, timeout=120)
    o = out.read().decode().strip()
    e = err.read().decode().strip()
    print(f"$ {cmd[:60]}...")
    if o:
        print(o)
    if e:
        print("[err]", e[:300])
    print()
c.close()
