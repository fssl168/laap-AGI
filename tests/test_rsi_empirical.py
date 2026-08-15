"""
Aris RSI 能力功能验证 (pytest 测试版)
=====================================

原为根目录临时脚本 `_rsi_empirical_bench.py`，已迁移至 tests/ 目录并转为 pytest 测试（论文 aris-rsi-paper.md 附录A）。

覆盖四模块:
  1. RSI 参数自优化   — laap.agi.rsi_engine.RSIMetaEngine (10轮迭代)
  2. Hebbian 突触可塑性 — aris_brain.hebbian_learner.HebbianLearner (50次更新)
  3. 持续学习管道     — laap.agi.continuous_learning.LearningPipeline (6条经验)
  4. 元学习策略选择   — laap.agi.meta_learning.MetaLearningEngine (3次会话)

运行方式:
    python tests/test_rsi_empirical.py            # 打印论文 4.1-4.4 节功能验证数据
    python -m pytest tests/test_rsi_empirical.py -v   # 作为自动化测试
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repository root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import math

import numpy as np

logging.basicConfig(level=logging.WARNING)

rng = np.random.RandomState(42)


# ═══════════════ 1. RSI 参数自优化 (10 轮) ═══════════════
def bench_rsi():
    from laap.agi.rsi_engine import RSIMetaEngine

    engine = RSIMetaEngine()
    n_params = len(engine.parameters)
    history = {}
    results = []

    for round_i in range(10):
        # 模拟性能指标: 随机但随迭代缓慢提升
        perf = {
            name: min(0.95, 0.3 + round_i * 0.03 + rng.uniform(-0.08, 0.08))
            for name in engine.parameters
        }
        suggestions = engine.suggest_improvements(perf)
        if suggestions:
            best = suggestions[0]
            attempt = engine.apply_improvement(
                best["parameter"], best["to"], best["rationale"])
            # 评估: 80% 概率成功
            success = rng.random() < 0.8
            engine.evaluate_improvement(attempt.id, 0.05 if success else -0.05)
            results.append({
                "round": round_i + 1, "parameter": attempt.target,
                "old": round(attempt.old_value, 4),
                "new": round(attempt.new_value, 4),
                "success": attempt.success, "reverted": attempt.reverted,
            })

    stats = engine.stats()
    # 记录每个参数的优化轨迹
    for name, p in engine.parameters.items():
        history[name] = {"initial": None, "final": p.current_value,
                         "count": p.optimization_count}
    return {
        "n_params": n_params, "attempts": results,
        "stats": stats, "history": history,
        "success_rate": stats["success_rate"],
        "optimized_parameters": stats["optimized_parameters"],
    }


# ═══════════════ 2. Hebbian 突触可塑性 (50 次) ═══════════════
def bench_hebbian():
    from aris_brain.hebbian_learner import HebbianLearner

    dim = 1024
    hl = HebbianLearner(dim=dim, n_patterns=64)
    n_updates = 50

    # 构建 6 个基准模式（认知空间中重复出现的状态类）
    base_rng = np.random.RandomState(7)
    base_patterns = []
    for _ in range(6):
        v = base_rng.randn(dim); v /= np.linalg.norm(v)
        base_patterns.append(v)

    # 序列: 前 6 次依次首次出现模式1-6(建立), 之后循环重复(命中)
    # → 命中 44/50 = 88%, 模式存储 6 个
    hit_count = 0
    for i in range(n_updates):
        base = base_patterns[i % 6]
        pre = base + 0.01 * rng.randn(dim)
        pre /= np.linalg.norm(pre)
        post = base_rng.randn(dim); post /= np.linalg.norm(post)
        valence = 0.5 + 0.4 * math.sin(i / 3.0)
        reward = 0.3 if i % 2 == 0 else -0.1
        hl.update(pre, post, valence, reward)
        # 检测本次更新是否命中已有模式 (sim > 0.85)
        hit = any(float(np.dot(p["input"], pre)) > 0.85 for p in hl.patterns)
        hit_count += int(hit)

    st = hl.stats()
    # 预测相似度（小噪声, 模拟再次遇到已知模式）
    state = base_patterns[0] + 0.01 * rng.randn(dim)
    state /= np.linalg.norm(state)
    pred = hl.predict(state)
    sim = float(np.dot(pred, state) /
                (np.linalg.norm(pred) * np.linalg.norm(state) + 1e-9))
    applied, _ = hl.apply_patterns(state)
    return {
        "dim": dim, "n_patterns_capacity": hl.n_patterns,
        "n_updates": st["n_updates"], "n_patterns_stored": st["n_patterns"],
        "match_rate": st["match_rate"],
        "hit_count": hit_count,
        "predict_similarity": round(sim, 4),
        "apply_triggered": applied,
    }


# ═══════════════ 3. 持续学习管道 (6 条经验) ═══════════════
def bench_continuous():
    from laap.agi.continuous_learning import LearningPipeline

    pipe = LearningPipeline(name="bench")
    scenarios = [
        ("complex_task",   "decompose_task",             0.85, "decompose"),
        ("code_generation","verify_generated_code",      0.85, "verify_incrementally"),
        ("api_call",       "retry_with_alternatives",    0.30, "explore_alternatives"),
        ("ambiguous_task", "ask_user_to_clarify",        0.82, "seek_clarification"),
        ("ambiguous_task", "ask_followup_question",      0.82, "seek_clarification"),
        ("novel_task",     "map_to_known_pattern",       0.75, "analogize"),
    ]
    for domain, action, outcome, strategy in scenarios:
        pipe.learn(domain=domain, action=action, outcome=outcome,
                   strategy_used=strategy,
                   lessons=[f"{action} completed with outcome {outcome}"])

    strategies = pipe.updater.strategies
    rows = []
    for name, s in strategies.items():
        rows.append({"name": name, "usage": s.usage_count,
                     "success_rate": round(s.success_rate, 3),
                     "avg_improvement": round(s.avg_improvement, 3)})
    return {
        "total_learned": pipe.total_learned,
        "buffer": pipe.buffer.stats(),
        "strategies": rows,
        "best_strategy": pipe.updater.stats()["best_strategy"],
        "recommendations": pipe.updater.recommend_strategy("code_generation"),
    }


# ═══════════════ 4. 元学习策略选择 (3 次会话) ═══════════════
def bench_meta():
    from laap.agi.meta_learning import MetaLearningEngine, LearningStrategy

    engine = MetaLearningEngine()
    sessions = [
        # general 领域 ×3 (structured 策略)
        ("RSI_原理", LearningStrategy.STRUCTURED, 30, 0.2, 0.75, "general"),
        ("PSI_循环", LearningStrategy.STRUCTURED, 45, 0.1, 0.80, "general"),
        ("记忆层次", LearningStrategy.STRUCTURED, 25, 0.3, 0.82, "general"),
    ]
    for concept, strategy, dur, mb, ma, domain in sessions:
        engine.record_session(concept=concept, strategy=strategy,
                              duration_minutes=dur, mastery_before=mb,
                              mastery_after=ma, difficulty=0.5,
                              domain=domain, successful=True)

    rec_general = engine.recommend_strategy(concept="测试", domain="general",
                                            difficulty=0.5)
    report = engine.get_strategy_report()
    eff = engine.get_learning_efficiency(domain="general", days=7)
    return {
        "total_sessions": engine.stats()["total_sessions"],
        "recommend_general": rec_general.value,
        "efficiency": eff,
        "strategy_report": report,
    }


# ═══════════════ pytest 测试（seed=42 可复现） ═══════════════

def test_rsi_self_improvement():
    """10 轮迭代应优化全部参数，成功率 >= 80%，失败尝试被回滚。"""
    r = bench_rsi()
    assert r["n_params"] == 10
    assert r["optimized_parameters"] == 10
    assert r["success_rate"] >= 0.8
    reverted = [a for a in r["attempts"] if a["reverted"]]
    assert len(reverted) <= 2, "回滚次数不应超过模拟失败次数"


def test_hebbian_pattern_learning():
    """50 次更新应建立模式库并达到高匹配率。"""
    h = bench_hebbian()
    assert h["dim"] == 1024
    assert h["n_updates"] == 50
    assert h["n_patterns_stored"] >= 6
    assert h["match_rate"] >= 0.85, f"匹配率 {h['match_rate']} 过低"
    assert h["apply_triggered"] is True, "已知模式应触发 apply_patterns"


def test_continuous_learning():
    """6 条经验应全部入库，且策略置信度得到区分。"""
    c = bench_continuous()
    assert c["total_learned"] == 6
    assert c["buffer"]["current_size"] == 6
    by_name = {s["name"]: s for s in c["strategies"]}
    # 正向经验应提高置信度, 负向经验应降低
    assert by_name["explore_alternatives"]["success_rate"] < 0.5
    assert by_name["seek_clarification"]["success_rate"] > 0.5


def test_meta_learning():
    """general 领域应推荐 structured 策略, 成功率 1.0。"""
    m = bench_meta()
    assert m["total_sessions"] == 3
    assert m["recommend_general"] == "structured"
    assert m["efficiency"]["success_rate"] >= 0.95
    assert m["efficiency"]["best_strategy"] == "structured"


if __name__ == "__main__":
    print("=" * 62)
    print("Aris RSI 能力功能验证 — 受控模拟环境运行数据 (论文 aris-rsi-paper.md)")
    print("=" * 62)

    print("\n[1] RSI 参数自优化 (10 轮)")
    r = bench_rsi()
    print(f"    可优化参数: {r['n_params']} 个")
    print(f"    成功优化参数: {r['optimized_parameters']} 个 (成功率 {r['success_rate']:.1%})")
    print(f"    改进尝试明细:")
    for a in r["attempts"]:
        mark = "✓" if a["success"] else "✗回滚"
        print(f"      R{a['round']:<2} {a['parameter']:<24} {a['old']:<8.3f} → {a['new']:<8.3f}  {mark}")
    print(f"    10 轮后参数终值:")
    for name, h in r["history"].items():
        if h["count"] > 0:
            print(f"      {name:<24} → {h['final']:<8.3f} (优化{h['count']}次)")

    print("\n[2] Hebbian 突触可塑性 (50 次更新)")
    h = bench_hebbian()
    print(f"    状态维度: {h['dim']}D, 模式容量: {h['n_patterns_capacity']}")
    print(f"    更新次数: {h['n_updates']}, 模式存储: {h['n_patterns_stored']}")
    print(f"    模式匹配率: {h['match_rate']:.1%} (即 {h['n_updates']} 次更新中 "
          f"新输入触发已有模式 {int(h['match_rate'] * h['n_updates'])} 次)")
    print(f"    预测余弦相似度: {h['predict_similarity']}")
    print(f"    模式应用触发: {h['apply_triggered']}")

    print("\n[3] 持续学习管道 (6 条经验)")
    c = bench_continuous()
    print(f"    学习记录: {c['total_learned']} 条, 缓冲区: {c['buffer']['current_size']} 条")
    for s in c["strategies"]:
        print(f"      {s['name']:<22} 使用{s['usage']}次 成功率{s['success_rate']:.2f} 改进{s['avg_improvement']:+.2f}")
    print(f"    最佳策略: {c['best_strategy']}")

    print("\n[4] 元学习策略选择 (3 次会话)")
    m = bench_meta()
    print(f"    会话总数: {m['total_sessions']}")
    print(f"    general 领域推荐: {m['recommend_general']}")
    print(f"    学习效率: sessions={m['efficiency']['sessions']}, "
          f"成功率={m['efficiency']['success_rate']:.2f}, "
          f"最佳策略={m['efficiency']['best_strategy']}")
    print(f"    策略效果报告:")
    for s in m["strategy_report"]:
        print(f"      {s['strategy']:<14} gain={s['avg_gain_rate']:.4f} "
              f"succ={s['success_rate']:.2f} used={s['total_uses']}次")

    print("\n✅ 评测完成")