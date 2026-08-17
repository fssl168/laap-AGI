# -*- coding: utf-8 -*-
"""同步第二批修复到 NAS（2026-08-18 18:xx）:
  - aris_brain/paper_trading_tools.py  (_brief PG 兼容 date()→参数化 + _backtest 修复)
  - tests/conftest.py                   (强制 sqlite 隔离)
  - tests/test_risk_gate.py             (loss_streak 测试数据修复)
  - tests/test_sector_reports.py        (fixture _db 指向 tmp)
"""
import hashlib
import io
import paramiko

HOST, PORT, USER, PASS = "192.168.88.251", 18922, "wolf", "Qiu@121236"
NAS_ROOT = "/vol1/@appdata/trim.hermes/workspace/laap-AGI"
LOCAL_ROOT = r"D:\laap-AGI"

FILES = [
    "aris_brain/paper_trading_tools.py",
    "tests/conftest.py",
    "tests/test_risk_gate.py",
    "tests/test_sector_reports.py",
]


def md5_norm(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS,
              timeout=15, banner_timeout=15, auth_timeout=15,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    ts = "20260818_2"
    for rel in FILES:
        # 备份 NAS 旧版
        try:
            c.exec_command(f"cp {NAS_ROOT}/{rel} {NAS_ROOT}/{rel}.bak_{ts}")
        except Exception:
            pass
        # 上传
        local = LOCAL_ROOT + "\\" + rel.replace("/", "\\")
        with open(local, "rb") as f:
            data = f.read()
        local_md5 = md5_norm(data)
        sftp.putfo(io.BytesIO(data), f"{NAS_ROOT}/{rel}")
        with sftp.open(f"{NAS_ROOT}/{rel}", "rb") as f:
            remote_md5 = md5_norm(f.read())
        status = "OK" if local_md5 == remote_md5 else "MD5-MISMATCH!"
        print(f"  [{status}] {rel}")
    sftp.close()
    print("上传完成")
    c.close()


if __name__ == "__main__":
    main()
