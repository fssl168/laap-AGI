"""UnifiedMemory 持久化（save/load）回归测试。

覆盖：encode → save → 新建实例 load → 恢复相同数量的 episodes，
确保记忆跨进程/重启不丢（为解决"最近经历"重启即清的根因而引入）。
"""
from laap.agi.unified_memory import UnifiedMemory


def _make(tmp_path, monkeypatch):
    # 将该实例的持久化路径重定向到临时目录，避免污染生产 laap/data
    path = tmp_path / "unified_memory.json"
    monkeypatch.setattr(UnifiedMemory, "PERSIST_PATH", path)
    m = UnifiedMemory()
    return m, path


def test_save_load_roundtrip_preserves_episodes(tmp_path, monkeypatch):
    m, path = _make(tmp_path, monkeypatch)
    # 初始应为空（临时目录无文件）
    assert len(m.episodic_memory.episodes) == 0

    # encode 2 条经历并自动 save
    m.encode_experience("开仓 600519 BUY 100 股 @1850.00", emotional_valence=0.0,
                        emotional_arousal=0.4, context_triggers=["600519", "buy"])
    m.encode_experience("[trend_follow] 600519 交易结果 pnl=-2.00% hold=2d: 趋势止损",
                        emotional_valence=-0.3, emotional_arousal=0.5,
                        context_triggers=["600519", "trend_follow"])
    assert path.exists(), "save 应已把记忆写入持久化文件"

    # 新建实例 → __init__ 应 load 恢复
    m2 = UnifiedMemory()
    eps = m2.get_recent_episodes(hours=24 * 7, max_results=20)
    assert len(eps) == 2, f"应恢复 2 条，实际 {len(eps)}"
    contents = [e["content"] for e in eps]
    assert any("开仓 600519" in c for c in contents)
    assert any("短" in c or "trend" in c.lower() for c in contents)


def test_load_without_file_is_silent(tmp_path, monkeypatch):
    m, _ = _make(tmp_path, monkeypatch)
    # 无文件时 load 静默返回 False、不抛错
    assert not path_exists(m.PERSIST_PATH)
    assert m.load() is False
    assert len(m.episodic_memory.episodes) == 0


def path_exists(p):
    import os
    return os.path.exists(str(p))
