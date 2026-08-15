# -*- coding: utf-8 -*-
"""D3 真实 LLM 判定抽查集运行（用户环境/联网时执行）。

对内置人工标注抽查集（test_news_eval.ANNOTATED_SAMPLES）跑真实 LLM 判定，
输出一致率 + 混淆矩阵 + 是否建议开启自动下单（一致率 ≥70%）。

用法:
    python scripts/run_news_eval_real.py
    python scripts/run_news_eval_real.py --n 20      # 只用前 N 条
    python scripts/run_news_eval_real.py --model deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _llm_call_factory(base: str, key: str, model: str):
    """OpenAI 兼容 chat/completions 适配到 news_verifier 契约 (prompt, system, max_tokens)。"""
    url = base.rstrip("/") + "/chat/completions"

    def _call(prompt: str, system: str = "", max_tokens: int = 800):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = json.dumps({"model": model, "messages": messages,
                           "max_tokens": max_tokens}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        # 取 content（reasoning 模型可能把推理放 reasoning_content）
        return d["choices"][0]["message"].get("content", "") or ""

    return _call


def main() -> int:
    ap = argparse.ArgumentParser(description="D3 真实 LLM 判定抽查集")
    ap.add_argument("--n", type=int, default=0, help="只用前 N 条（0=全部）")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "deepseek-v4-flash"))
    ap.add_argument("--base", default=os.environ.get("DEEPSEEK_BASE_URL",
                                                     "https://api.deepseek.com"))
    ap.add_argument("--key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    args = ap.parse_args()

    if not args.key:
        print("[FAIL] 未配置 DEEPSEEK_API_KEY（.env）")
        return 1

    from tests.test_news_eval import (
        ANNOTATED_SAMPLES, compute_consistency, suggest_auto_order)

    samples = ANNOTATED_SAMPLES[:args.n] if args.n > 0 else ANNOTATED_SAMPLES
    print(f"D3 真实 LLM 判定抽查集 | model={args.model} | 样本数={len(samples)}")
    print("=" * 60)
    llm_call = _llm_call_factory(args.base, args.key, args.model)
    r = compute_consistency(samples, llm_call)
    print(f"一致率: {r['consistency']:.1%} ({r['correct']}/{r['n']})")
    print("混淆矩阵 (人工判定 → LLM 判定):")
    for expected, actual_map in r["confusion"].items():
        print(f"  {expected}: {actual_map}")
    ok, reason = suggest_auto_order(r["consistency"])
    print(f"\n结论: {reason}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
