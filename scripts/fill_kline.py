"""补全 watchlist_kline_store 中缺失的 K 线数据。"""
import os, sys, json
sys.path.insert(0, 'D:/laap-AGI')
os.chdir('D:/laap-AGI')
from dotenv import load_dotenv
load_dotenv()

from laap.paper_trading import quant_config as qc
from watchlist_kline_store import upsert_kline, get_kline
import urllib.request

def load_from_tushare(symbol: str, days: int = 60) -> list:
    """从 Tushare 加载日线数据。"""
    token = os.environ.get('TUSHARE_TOKEN', '').strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN empty")
    
    ts_code = f"{symbol}.SH" if symbol.startswith(("6", "9")) else f"{symbol}.SZ"
    body = json.dumps({
        "api_name": "daily", "token": token,
        "params": {"ts_code": ts_code, "start_date": "", "end_date": ""},
        "fields": "trade_date,open,high,low,close,vol",
    }).encode("utf-8")
    req = urllib.request.Request(
        qc.TUSHARE_BASE_URL, data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    
    if d.get("code") != 0:
        raise RuntimeError(f"Tushare error: {d.get('msg')}")
    
    data = d.get("data") or {}
    items = data.get("items", [])
    if not items:
        return []
    
    # Tushare 返回降序（最新在前），取最近 days 条并反转成升序
    rows = items[:days][::-1]
    out = []
    for row in rows:
        out.append((row[0], row[1], row[2], row[3], row[4], row[5]))  # date, open, high, low, close, vol
    return out

def main():
    # 目标股票（从之前分析中无 kline 数据的）
    target_stocks = ['000001', '300750', '002594', '600144', '600511', '600133', 
                     '000523', '600038', '000410', '000957', '603663', '603728', '600999']
    
    all_rows = []
    success = []
    failed = []
    
    for sym in target_stocks:
        # 检查现有数据
        prefix = 'sz' if sym.startswith(('0', '3')) else 'sh'
        code = f'{prefix}{sym}'
        existing = get_kline(code, days=5)
        if len(existing) >= 5:
            print(f'{sym}: skip (已有 {len(existing)} rows)')
            continue
        
        # 从 Tushare 加载
        try:
            tushare_rows = load_from_tushare(sym, days=60)
            if not tushare_rows:
                print(f'{sym}: empty from tushare')
                failed.append(sym)
                continue
            
            # 转换格式: (code, date, open, close, high, low, volume)
            for row in tushare_rows:
                date, open_p, high, low, close, vol = row
                all_rows.append((code, date, open_p, close, high, low, vol))
            
            print(f'{sym}: loaded {len(tushare_rows)} rows')
            success.append(sym)
        except Exception as e:
            print(f'{sym}: error - {e}')
            failed.append(sym)
    
    # 批量写入
    if all_rows:
        print(f'\nWriting {len(all_rows)} rows to DB...')
        inserted = upsert_kline(all_rows)
        print(f'Inserted: {inserted} rows')
    else:
        print('No data to write')
    
    print(f'\n=== Summary ===')
    print(f'Success: {success}')
    print(f'Failed: {failed}')

if __name__ == '__main__':
    main()
