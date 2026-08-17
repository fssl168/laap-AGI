# -*- coding: utf-8 -*-
"""清理 laap_trading 中 verify 脚本污染的测试数据(已备份)。

清理规则:
  - signals: rationale IN ('', 'test buy', 't') 或 LIKE '测试买入%' → 测试信号
  - orders: client_request_id IS NULL → 测试订单(正规路径都有决策键)
  - trades: 关联上述订单的成交
  - net_values: 异常快照(total < 10万 或 > 200万) → 保留 100 万附近的正常快照
保留: 正常策略信号/订单/成交/决策。
"""
import psycopg

conn = psycopg.connect(host="192.168.88.251", port=54322,
                       user="fileclaw", password="fileclaw_secret",
                       dbname="laap_trading", connect_timeout=5)
conn.autocommit = True
cur = conn.cursor()

# 1. 找出测试信号 id
cur.execute("""
    SELECT id FROM signals
    WHERE rationale IN ('', 'test buy', 't')
       OR rationale LIKE '测试买入%'
""")
test_sig_ids = [r[0] for r in cur.fetchall()]
print("测试信号:", len(test_sig_ids))

# 2. 找出测试订单(无决策键)及其 id
cur.execute("SELECT id FROM orders WHERE client_request_id IS NULL")
test_ord_ids = [r[0] for r in cur.fetchall()]
print("测试订单:", len(test_ord_ids))

# 3. 测试成交 = 关联测试订单 或 order_id 在测试订单中
cur.execute("SELECT COUNT(*) FROM trades WHERE order_id IN (SELECT id FROM orders WHERE client_request_id IS NULL)")
test_trd = cur.fetchone()[0]
print("关联测试成交:", test_trd)

# 4. 删除(先删 trades → orders → signals)
if test_ord_ids:
    cur.execute(
        "DELETE FROM trades WHERE order_id IN (SELECT id FROM orders WHERE client_request_id IS NULL)")
    print("删除 trades:", cur.rowcount)
    cur.execute("DELETE FROM orders WHERE client_request_id IS NULL")
    print("删除 orders:", cur.rowcount)
if test_sig_ids:
    cur.execute(
        "DELETE FROM signals WHERE id = ANY(%s)", (test_sig_ids,))
    print("删除 signals:", cur.rowcount)

# 5. 清理异常净值快照(保留 100 万附近正常值)
cur.execute("SELECT COUNT(*) FROM net_values WHERE total < 100000 OR total > 2000000")
weird = cur.fetchone()[0]
if weird:
    cur.execute("DELETE FROM net_values WHERE total < 100000 OR total > 2000000")
    print("删除异常净值:", cur.rowcount)

# 6. 汇总
for t in ["signals", "orders", "trades", "net_values", "decisions", "risk_rejections"]:
    cur.execute("SELECT COUNT(*) FROM " + t)
    print(f"{t}: {cur.fetchone()[0]}")

cur.close()
conn.close()
print("清理完成")
