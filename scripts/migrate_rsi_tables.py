# -*- coding: utf-8 -*-
"""补迁本地 data/laap.db 的 rsi_goals/rsi_attempts → PG laap 库 + 清理 PG laap 交易空壳表

背景 (2026-08-18): 本地 rsi_goals(10)/rsi_attempts(2) PG 缺失; PG laap 库有 11 个
交易空壳表(0行, 冗余)。本脚本: ① UPSERT 迁移 rsi 两表 ② DROP 交易空壳表。

用法: PYTHONPATH= ./.venv/Scripts/python.exe scripts/migrate_rsi_tables.py
"""
import sqlite3
import psycopg

LOCAL = r"D:\laap-AGI\data\laap.db"
PG = dict(host="192.168.88.251", port=54322, user="fileclaw",
          password="fileclaw_secret", dbname="laap", connect_timeout=8)

RSI_TABLES = ["rsi_goals", "rsi_attempts"]
# PG laap 库中冗余的交易空壳表（真实交易数据在 laap_trading 库）
SHELL_TABLES = ["decisions", "evolutions", "net_values", "news_items",
                "news_summaries", "news_verdicts", "orders", "outcomes",
                "risk_rejections", "signals", "trades"]


def migrate_rsi():
    conn = sqlite3.connect(LOCAL)
    c = psycopg.connect(**PG)
    cur = c.cursor()
    for t in RSI_TABLES:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        if not rows:
            print(f"{t}: 本地无数据, 跳过")
            continue
        cur.execute(f"SELECT id FROM {t}")
        existing = {r[0] for r in cur.fetchall()}
        ins = upd = 0
        placeholders = ", ".join(["%s"] * len(cols))
        colnames = ", ".join(cols)
        updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in cols if col != "id")
        for row in rows:
            cur.execute(
                f"INSERT INTO {t} ({colnames}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates}", tuple(row))
            if row[0] in existing:
                upd += 1
            else:
                ins += 1
        c.commit()
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        total = cur.fetchone()[0]
        print(f"{t}: 插入 {ins}, 覆盖 {upd}, PG 总行数 {total}")
    c.close()
    conn.close()


def drop_shell_tables():
    c = psycopg.connect(**PG)
    cur = c.cursor()
    for t in SHELL_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = cur.fetchone()[0]
        if n == 0:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
            print(f"DROP 空壳表 {t} (0行)")
        else:
            print(f"跳过 {t}: 有 {n} 行数据, 不删")
    c.commit()
    c.close()


if __name__ == "__main__":
    migrate_rsi()
    print()
    drop_shell_tables()
