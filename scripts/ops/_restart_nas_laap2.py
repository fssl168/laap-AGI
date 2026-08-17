# -*- coding: utf-8 -*-
"""通过 sudo -S 重启 NAS LAAP（密码经 stdin，不加 /dev/null）。"""
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, port=PORT, username=USER, password=PASS,
          timeout=15, banner_timeout=15, auth_timeout=15,
          allow_agent=False, look_for_keys=False)

cmd = f"sudo -S bash {NAS_ROOT}/start-laap-nas.sh"
stdin, stdout, stderr = c.exec_command(cmd, timeout=150)
stdin.write(PASS + "\n")
stdin.flush()
stdin.channel.shutdown_write()  # 关闭写端，让脚本正常跑
o = stdout.read().decode()
e = stderr.read().decode()
print("STDOUT:", o[-2000:])
if e:
    print("STDERR:", e[-400:])
c.close()
