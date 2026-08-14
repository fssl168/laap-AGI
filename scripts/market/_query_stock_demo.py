# -*- coding: utf-8 -*-
from pathlib import Path
"""调用LAAP查询600114实时股价"""
import json
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

API = "http://localhost:11546/v1/chat/completions"

TOOLS = [{"type": "function", "function": {
    "name": "get_stock_price",
    "description": "Query real-time stock price from Tencent finance API. 查询A股实时行情，支持代码查询。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码，如600114"}
        },
        "required": ["code"]
    }
}}]

def fetch_stock(code: str) -> dict:
    """调用腾讯接口获取股价"""
    import re
    symbol = f"{'sh' if code.startswith('6') else 'sz'}{code}"
    req = urllib.request.Request(
        f"http://qt.gtimg.cn/q={symbol}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "http://finance.qq.com/"}
    )
    raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="ignore")
    m = re.search(r'v_\w+="([^"]+)"', raw)
    if not m:
        return {"error": "未找到数据"}
    f = m.group(1).split("~")
    return {
        "code": code,
        "name": f[1],
        "price": float(f[3]),
        "prev_close": float(f[4]),
        "open": float(f[5]),
        "high": float(f[33]),
        "low": float(f[34]),
        "change": float(f[31]),
        "change_pct": float(f[32]),
        "volume": float(f[6]),
        "time": f[30]
    }

def main():
    payload = {
        "model": "laap-core",
        "messages": [{"role": "user", "content": "查询600114的股价"}],
        "tools": TOOLS,
        "max_tokens": 500
    }
    
    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    d = json.load(urllib.request.urlopen(req, timeout=30))
    c = d["choices"][0]
    
    print(f"finish: {c['finish_reason']} | engine: {d.get('engine')}")
    
    if c["finish_reason"] == "tool_calls":
        tc = c["message"]["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        print(f"tool_calls -> {tc['function']['name']} {tc['function']['arguments']}")
        
        # 执行查询
        result = fetch_stock(args["code"])
        print(f"\n查询结果:")
        print(f"  股票: {result.get('name')}~{result['code']}~")
        print(f"  当前价: {result['price']:.2f} 元 (昨收 {result['prev_close']:.2f})")
        print(f"  涨跌: {result['change']:+.2f} ({result['change_pct']:+.2f}%)")
        print(f"  今开: {result['open']:.2f} | 最高: {result['high']:.2f} | 最低: {result['low']:.2f}")
        print(f"  成交量: {result['volume']/10000:.0f}万手 | 时间: {result['time']}")
        
        # 回填结果
        msgs = [
            {"role": "user", "content": "查询600114的股价"},
            {"role": "assistant", "content": None, "tool_calls": [tc]}
        ]
        msgs.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps(result, ensure_ascii=False)
        })
        
        payload2 = {"model": "laap-core", "messages": msgs, "max_tokens": 500}
        req2 = urllib.request.Request(API, data=json.dumps(payload2).encode(), 
                                      headers={"Content-Type": "application/json"})
        d2 = json.load(urllib.request.urlopen(req2, timeout=30))
        print(f"\nLAAP回答:\n{d2['choices'][0]['message']['content']}")
    else:
        print(f"content: {c['message'].get('content', '')[:200]}")

if __name__ == "__main__":
    main()
