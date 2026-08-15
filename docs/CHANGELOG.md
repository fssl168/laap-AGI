# Changelog

本文件记录用户可见能力 / API / 部署 / 通知 / 报告结构等变更。`[Unreleased]` 段使用扁平格式（`- [类型] 描述`），发版时由 maintainer 汇总。

## [Unreleased]

- [新功能] news-intel 闭环：新闻数据获取（akshare 双源兜底+重试）→ LLM+RSI 判定（fail-closed）→ 研报策略（含费预算）→ 风控门（R1-R5）→ 自动下单（ledger 真实扣费）→ 留痕（news_items/verdicts/rejections/summaries 表 + 5 条 API 路由）
- [新功能] Tushare 数据源接入：K线链启用 tushare（db→tushare→akshare→synthetic）；新闻链新增 `tushare` 快讯源（news API，TUSHARE_NEWS_SRC/LOOKBACK_HOURS 可调，需单独权限，失败 fail-closed 回退）
- [新功能] NewsSignalWorker 盘中轮询（LAAP_NEWS_INTRADAY=1 启用，B5 时段 + B6 新鲜度校验 + D1 去重控成本）
- [新功能] D3 判定评估框架（人工抽查集 20 条，真实 LLM 一致率 95% ≥70% 门槛）
- [新功能] 回测引擎三项修复：次日开盘成交（消除同收盘自引用）/ 默认 A 股成本 / 路径级 z 显著性
- [新功能] ledger 交易成本扣费（fee_model：买入佣金+过户费+滑点，卖出佣金+印花税+过户费+滑点，pnl 净额）
- [新功能] 成本单源 `costs.py`（回测与 ledger 费率一致，消除双口径）
- [新功能] `quant_config.py` 运行时可调参数（模块属性访问实时读 env）
- [改进] 契约检查/漂移修复：judge 签名、open_positions、ATR、D1 去重、LLM 适配器
- [改进] news 管线行情降级 fail-closed：实时价降级（stub/无源）时自动下单被拒（仅出计划+留痕，绝不用合成价成交）；R1 风控门补「止损位高于成交价」拦截（计划价位与成交价不一致防护）
- [新增] `scripts/e2e_news_pipeline_real.py` 真实全管线 E2E（新闻→LLM 判定→研报策略→风控门→自动下单→留痕，支持 --auto-order/--inject-bullish/--force-now/--fake-market）
- [文档] 新增 AGENTS.md（项目全局行为准则）与 news-intel 计划/复核文档
- [chore] 量化本地文件（laap/paper_trading/）不进 git（NAS 不同步约定）
