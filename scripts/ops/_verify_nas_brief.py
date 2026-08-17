# -*- coding: utf-8 -*-
"""验证 NAS 端 _brief 修复（PG 兼容 SQL）。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

CMD = f"""cd {NAS_ROOT} && ./.venv/bin/python -c "
import urllib.request, json
key='sk-unsloth-4257b387b9b16f798969c35ccc099db6'
for q in ['今日交易简报', '跑回测']:
    payload={{'model':'laap-core','messages':[{{'role':'user','content':q}}]}}
    req=urllib.request.Request('http://127.0.0.1:11546/v1/chat/completions',
      data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
      headers={{'Content-Type':'application/json','Authorization':'Bearer '+key}})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d=json.loads(r.read().decode('utf-8'))
        content=(d.get('choices',[{{}}])[0].get('message',{{}}).get('content','') or '')
        print('Q:', q)
        print('  engine:', d.get('engine'))
        print('  reply:', content[:300].replace(chr(10),' | '))
        print()
    except urllib.error.HTTPError as e:
        print('Q:', q, 'HTTP', e.code, e.read().decode()[:200])
" 2>&1"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)
_, out, err = c.exec_command(CMD, timeout=150)
o = out.read().decode()
e = err.read().decode()
print(o[-2000:])
if e:
    print("[err]", e[-300:])
c.close()
