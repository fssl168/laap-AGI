"""Live 源真实验证脚本（用户环境运行，增强 2）。

沙箱联网受限无法真实验证 akshare 取价；此脚本在用户环境运行，
验证真实行情取价 + 降级路径。

用法:
    python scripts/verify_live_source.py [symbol ...]
    # 默认 600519 000001
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from laap.paper_trading.market_source import LiveMarketSource, resolve_source

    symbols = sys.argv[1:] or ["600519", "000001"]
    live = LiveMarketSource()
    ok_all = True
    for sym in symbols:
        try:
            price, meta = live.get_price(sym)
            print(f"[OK] {sym}: price={price} meta={meta}")
        except Exception as e:
            # get_price 已内置 stub 降级，此处不应抛；抛则说明降级失败
            ok_all = False
            print(f"[FAIL] {sym}: {e}")

    src = resolve_source(prefer_live=True)
    print(f"resolve_source → {type(src).__name__}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
