# -*- coding: utf-8 -*-
"""D3 判定评估：人工抽查集一致性。

框架：内置人工标注新闻样本，跑 verify_news 对比 LLM 判定 vs 人工标注，
计算一致率。一致率 ≥0.7 才建议开启自动下单。

真实评估需用户环境跑真实 llm_call（此处用 stub 验证框架逻辑）。
"""

from typing import Callable, Dict, List, Tuple

from laap.paper_trading.news_verifier import (
    NewsItem, TechState, verify_news)

# 人工标注抽查集（20 条，覆盖 利好/假消息/中性/利空 + 不同 RSI 场景）
ANNOTATED_SAMPLES: List[Tuple[NewsItem, TechState, str]] = [
    # ── genuine_bullish（实质利好）──
    (NewsItem("600519", "茅台宣布核心产品提价10%", content="公司发布提价公告，预计全年业绩预增",
              source="公告", published_at="2026-08-15 09:00"),
     TechState("600519", rsi=55.0, close=1400.0), "genuine_bullish"),
    (NewsItem("000858", "五粮液中标某大型商超年度供应合同", content="合同金额约50亿元，占去年营收15%",
              source="上证报", published_at="2026-08-14"),
     TechState("000858", rsi=60.0, close=120.0), "genuine_bullish"),
    (NewsItem("600276", "公司拟以10亿元回购股份并注销", content="回购彰显管理层信心，增厚每股收益",
              source="公告", published_at="2026-08-13"),
     TechState("600276", rsi=45.0, close=45.0), "genuine_bullish"),
    (NewsItem("300750", "新产品电池获多家车企订单", content="在手订单排产至明年，产能利用率提升",
              source="财联社", published_at="2026-08-12"),
     TechState("300750", rsi=50.0, close=180.0), "genuine_bullish"),
    (NewsItem("601012", "控股股东拟增持1%股份", content="增持计划未来6个月内实施",
              source="公告", published_at="2026-08-11"),
     TechState("601012", rsi=40.0, close=40.0), "genuine_bullish"),
    (NewsItem("600036", "半年报净利同比增长15%，超出市场预期", content="零售银行财富管理业务强劲",
              source="公告", published_at="2026-08-10"),
     TechState("600036", rsi=48.0, close=35.0), "genuine_bullish"),
    # ── fake_news（假消息/已辟谣）──
    (NewsItem("600519", "网传茅台并购某酒企", content="公司澄清：该传闻不属实，无相关计划",
              source="财联社", published_at="2026-08-15"),
     TechState("600519", rsi=60.0, close=1400.0), "fake_news"),
    (NewsItem("000001", "传平安银行将被外资收购", content="公司辟谣：纯属谣言，从未接触",
              source="证券时报", published_at="2026-08-14"),
     TechState("000001", rsi=58.0, close=11.0), "fake_news"),
    (NewsItem("002594", "网传比亚迪固态电池量产", content="公司否认，尚处实验室阶段",
              source="财联社", published_at="2026-08-13"),
     TechState("002594", rsi=55.0, close=250.0), "fake_news"),
    (NewsItem("600030", "传中信证券合并某券商", content="公司澄清：不存在应披露而未披露事项",
              source="上证报", published_at="2026-08-12"),
     TechState("600030", rsi=52.0, close=25.0), "fake_news"),
    # ── neutral（中性/例行公告）──
    (NewsItem("600519", "公司向全资子公司无偿划转资产", content="内部资源整合，不改变合并报表范围",
              source="公告", published_at="2026-08-15"),
     TechState("600519", rsi=62.0, close=1400.0), "neutral"),
    (NewsItem("000858", "中国人寿加仓五粮液，中央汇金退出前十大股东", content="股东结构变化，无实质经营影响",
              source="公告", published_at="2026-08-14"),
     TechState("000858", rsi=55.0, close=120.0), "neutral"),
    (NewsItem("600276", "公司召开年度股东大会", content="审议通过常规议案",
              source="公告", published_at="2026-08-13"),
     TechState("600276", rsi=50.0, close=45.0), "neutral"),
    (NewsItem("300750", "机构调研：关注公司产能扩张进度", content="常规调研纪要",
              source="互动易", published_at="2026-08-12"),
     TechState("300750", rsi=48.0, close=180.0), "neutral"),
    (NewsItem("601012", "公司派发2025年度分红", content="每10股派3元，除息日临近",
              source="公告", published_at="2026-08-11"),
     TechState("601012", rsi=50.0, close=40.0), "neutral"),
    # ── bearish（利空）──
    (NewsItem("600519", "上半年净利润同比下滑1.95%", content="营收微增但利润下滑，盈利承压",
              source="公告", published_at="2026-08-15"),
     TechState("600519", rsi=65.0, close=1400.0), "bearish"),
    (NewsItem("000001", "控股股东拟减持不超过2%股份", content="减持计划6个月内实施",
              source="公告", published_at="2026-08-14"),
     TechState("000001", rsi=60.0, close=11.0), "bearish"),
    (NewsItem("002594", "公司因信息披露违规被立案调查", content="证监会立案，或被处罚",
              source="公告", published_at="2026-08-13"),
     TechState("002594", rsi=55.0, close=250.0), "bearish"),
    (NewsItem("600030", "Q2净利润环比下降36%", content="市场成交低迷拖累经纪与两融业务",
              source="公告", published_at="2026-08-12"),
     TechState("600030", rsi=50.0, close=25.0), "bearish"),
    (NewsItem("300750", "公司遭境外客户诉讼索赔", content="索赔金额或影响当期利润",
              source="公告", published_at="2026-08-11"),
     TechState("300750", rsi=45.0, close=180.0), "bearish"),
]


def compute_consistency(samples: List[Tuple[NewsItem, TechState, str]],
                        llm_call: Callable) -> Dict[str, object]:
    """跑 verify_news 与人工标注对比，返回一致率/混淆。

    Returns: {n, correct, consistency, confusion: {expected: {actual: count}}}
    """
    n = len(samples)
    correct = 0
    confusion: Dict[str, Dict[str, int]] = {}
    for item, ts, expected in samples:
        v = verify_news(item, None, ts, llm_call=llm_call)
        actual = v.verdict
        confusion.setdefault(expected, {}).setdefault(actual, 0)
        confusion[expected][actual] += 1
        if actual == expected:
            correct += 1
    consistency = correct / n if n else 0.0
    return {"n": n, "correct": correct, "consistency": round(consistency, 3),
            "confusion": confusion}


def suggest_auto_order(consistency: float) -> Tuple[bool, str]:
    """一致率 ≥0.7 → 建议开自动下单；否则不建议。"""
    if consistency >= 0.7:
        return True, f"一致率 {consistency:.0%} ≥ 70%，建议开启自动下单"
    return False, f"一致率 {consistency:.0%} < 70%，不建议开启自动下单"


def _perfect_oracle_llm() -> Callable:
    """stub：直接返回人工标注判定（确定性验证框架计算/门禁逻辑，不代表真实 LLM 质量）。

    真实 LLM 判定质量由 `scripts/run_news_eval_real.py` 用真实模型评估。
    """

    def _llm(prompt, system="", max_tokens=800):
        # 从 prompt 提取标题，匹配样本库的 expected
        title = ""
        if "新闻标题：" in prompt:
            title = prompt.split("新闻标题：")[1].split("\n")[0].strip()
        for item, _ts, expected in ANNOTATED_SAMPLES:
            if item.title == title:
                conf = 0.9 if expected != "neutral" else 0.6
                return {"verdict": expected, "confidence": conf,
                        "reasons": ["stub"], "impact": ""}
        return {"verdict": "neutral", "confidence": 0.5, "reasons": [],
                "impact": ""}
    return _llm


def test_eval_consistency_high():
    r = compute_consistency(ANNOTATED_SAMPLES, _perfect_oracle_llm())
    assert r["n"] == len(ANNOTATED_SAMPLES)
    assert r["consistency"] == 1.0  # perfect oracle → 框架计算/门禁正确
    ok, reason = suggest_auto_order(r["consistency"])
    assert ok is True


def test_suggest_auto_order_gate():
    assert suggest_auto_order(0.8)[0] is True
    assert suggest_auto_order(0.5)[0] is False
    assert suggest_auto_order(0.7)[0] is True  # 边界 ≥


def test_eval_confusion_recorded():
    r = compute_consistency(ANNOTATED_SAMPLES, _perfect_oracle_llm())
    assert r["confusion"]["genuine_bullish"]["genuine_bullish"] == 6
    assert r["confusion"]["fake_news"]["fake_news"] == 4
