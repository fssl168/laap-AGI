# aris_brain/_archive — 历史版本归档

> 本目录保留 **历史实现版本**，仅供代码考古与对照参考，**不参与构建、导入或测试**。
> 任何文件都不应被 `aris_brain/` 或其他模块引用。

## 内容

| 文件 | 说明 | 被替代者 |
|---|---|---|
| `aris_lm_v4.py` / `aris_lm_v41.py` | 早期中文理解引擎 | `aris_lm_v5.py`（现拆分为 `aris_lm_lexer/syntax/semantics/discourse`） |
| `aris_lm_v7.py` / `aris_lm_v8.py` / `aris_lm_v85.py` / `aris_lm_v86.py` / `aris_lm_v9.py` | v5→v10 之间的迭代版本 | 现役 `aris_lm_v11`（文件头标注 DEPRECATED 见 v11_agi_daemon） |
| `aris_pipeline_v2.py` / `v3.py` / `v4.py` | 认知管线早期实现 | `laap_brain/api.py` 的 `process_with_laap()` 多级链 |
| `ao_feishu_service.py` / `ao_v10_feishu_bridge.py` / `aris_feishu_bot.py` | 飞书机器人旧实现 | 外部 Hermes 运行时（hermes-agent） |
| `psilang_mini.py` | PSI 语言迷你实现 | `aris_brain/psi_core_bridge.py` |

## 维护纪律

- **不要**从本目录复制代码到现役模块（多为过期 API）。
- **不要**在 `import` 语句中引用本目录文件——它们不在包路径上。
- 如需清理：逐个判定后删除，删除前确认 `git log` 中有完整历史可回溯。

---
更新记录：2026-08（GAP-J）新增本说明，确认 14 个文件零引用，保留作历史对照。
