"""
LAAP AGI — 统一世界模型: 薄门面 (R11 拆分)
============================================================
原 world_model.py (1479 行) 已拆分为 world_model_defs / world_model_engine /
world_model_abstract / world_model_factory。本文件保留全部既有导入符号,
确保 `from laap.agi.world_model import ...` 零破坏。
"""

from .world_model_defs import (
    WorldModelType, EntityType, RelationType,
    PhysicalProperties, SpatialPos, SocialAttributes,
    Entity, Relation, CounterfactualBranch, SimulationResult,
    CommonsenseKnowledge,
)
from .world_model_engine import UnifiedWorldModel
from .world_model_abstract import (
    AbstractWorldModel, LocalWorldModel, QuantumWorldModelAdapter,
)
from .world_model_factory import _create_world_model_internal, create_world_model

__all__ = [
    "WorldModelType", "EntityType", "RelationType",
    "PhysicalProperties", "SpatialPos", "SocialAttributes",
    "Entity", "Relation", "CounterfactualBranch", "SimulationResult",
    "CommonsenseKnowledge", "UnifiedWorldModel",
    "AbstractWorldModel", "LocalWorldModel", "QuantumWorldModelAdapter",
    "_create_world_model_internal", "create_world_model",
]


# ─── 自测 (原 __main__ 冒烟) ───────────────────────────────────
def test():
    """完整功能测试"""
    wm = UnifiedWorldModel()
    logger.info("=== 测试1: 实体管理 ===")
    e = wm.add_entity("测试桌", EntityType.OBJECT,
                      phys=PhysicalProperties(mass=5.0, state="solid"))
    found = wm.find_entities(etype="object")
    logger.info(f"  默认实体: {len(wm.entities)} 个")
    logger.info(f"  物理对象: {[e.name for e in found]}")
    logger.info("\n=== 测试2: 社会关系 ===")
    sn = wm.social_network("lorry")
    logger.info(f"  Lorry 的社交网络: {len(sn['connections'])} 条连接")
    for c in sn['connections']:
        logger.info(f"    {c['from']} → {c['to']} ({c['relation']}, {c['strength']})")
    logger.info("\n=== 测试3: 反事实推理 ===")
    cf = wm.explore_counterfactual("water", "state", "gas",
                                   label="如果水是气态")
    logger.info(f"  分支: {cf.label}")
    logger.info(f"  概率: {cf.probability:.3f}, 一致性: {cf.coherence:.3f}")
    cf2 = wm.explore_counterfactual("lorry", "social.trust", 0.1,
                                    label="如果Lorry不信任Aris")
    logger.info(f"\n  分支: {cf2.label}")
    logger.info(f"  概率: {cf2.probability:.3f}, 一致性: {cf2.coherence:.3f}")
    logger.info("\n=== 测试4: 时间推理 ===")
    e = wm.get_entity("aris")
    e.add_history("said_hello", {"to": "lorry"})
    e.add_history("learned_causal", {"module": "causal.py"})
    tl = wm.get_entity_timeline("aris")
    logger.info(f"  Aris 历史事件: {len(tl)} 条")
    logger.info("\n=== 测试5: 社会关系更新 ===")
    wm.update_social_relation("aris", "lorry", trust_delta=0.05, affection_delta=0.02)
    aris = wm.get_entity("aris")
    logger.info(f"  Aris → Lorry: trust={aris.social.trust:.3f}, affection={aris.social.affection:.3f}")
    logger.info("\n=== 测试6: 世界模型统计 ===")
    for k, v in wm.stats().items():
        logger.info(f"  {k}: {v}")
    wm.save()
    logger.info(f"\n✅ 统一世界模型测试完成")
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test()
