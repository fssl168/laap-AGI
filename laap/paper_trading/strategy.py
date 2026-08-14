"""可进化的策略参数（code evolution 改进目标 + backtest 回测对象）。

这是增强 3 的"策略锚点"：CodeEvolutionEngine 扫描并改进本文件，
QuantEvolutionGate 通过 AST 提取 STRATEGY_PARAMS，做 mutation 前后 OOS 对比。
"""

# 均线交叉策略参数（short 短期窗口 / long 长期窗口）
STRATEGY_PARAMS = {
    "short": 5,   # 短期均线窗口
    "long": 20,   # 长期均线窗口
}
