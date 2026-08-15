#!/usr/bin/env bash
# ── NAS LAAP Brain API 启动脚本 ──────────────────────────────
# 用法: bash /vol1/@appdata/trim.hermes/workspace/laap-AGI/start-laap-nas.sh
# 功能:
#   1. 停止旧 LAAP 实例（若有）
#   2. 带 5 个环境变量启动 (M4 引擎 + M2 调度器 + 每日量化管线)
#   3. 输出验证信息
# 定时器说明: LAAP_QUANT_DAILY=1 时服务内置 QuantDailyScheduler
#   （daemon 线程, 交易日历判断, 交易日自动收集真实成交序列, 见
#   docs/paper-observation-runbook.md）
# ══════════════════════════════════════════════════════════════

set -euo pipefail

LAAP_ROOT="/vol1/@appdata/trim.hermes/workspace/laap-AGI"
PYTHON="${LAAP_ROOT}/.venv/bin/python"
PORT=11546

# ── 5 个环境变量 ──
export LAAP_TRSI_ENABLED=1              # M4 受限递归引擎
export LAAP_EVO_ENABLED=1               # M2 进化调度器
export LAAP_EVO_INTERVAL=3600           # 调度间隔（秒）
export LAAP_QUANT_DAILY=1               # 每日量化管线（交易日收集）
export LAAP_QUANT_DAILY_INTERVAL=86400  # 每日管线间隔（秒）

echo "[LAAP] 环境变量:"
echo "  LAAP_TRSI_ENABLED=${LAAP_TRSI_ENABLED}"
echo "  LAAP_EVO_ENABLED=${LAAP_EVO_ENABLED}"
echo "  LAAP_EVO_INTERVAL=${LAAP_EVO_INTERVAL}"
echo "  LAAP_QUANT_DAILY=${LAAP_QUANT_DAILY}"
echo "  LAAP_QUANT_DAILY_INTERVAL=${LAAP_QUANT_DAILY_INTERVAL}"

# ── 停止旧实例 ──
OLD_PID=$(pgrep -f "laap_brain.api --port ${PORT}" | head -1 || true)
if [ -n "${OLD_PID}" ]; then
    echo "[LAAP] 停止旧实例 PID=${OLD_PID}"
    kill "${OLD_PID}" 2>/dev/null || true
    sleep 2
fi

# ── 启动（nohup 后台）──
cd "${LAAP_ROOT}"
echo "[LAAP] 启动 LAAP Brain API (port ${PORT})..."
nohup "${PYTHON}" -m laap_brain.api --port "${PORT}" \
    > "${LAAP_ROOT}/laap-nas.log" 2>&1 &

# ── 等待就绪 ──
for i in $(seq 1 30); do
    if curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"status": "ok"'; then
        echo "[LAAP] 启动成功 ✓"
        echo "[LAAP] 日志: ${LAAP_ROOT}/laap-nas.log"
        exit 0
    fi
    sleep 1
done
echo "[LAAP] 警告: 30s 内未就绪，检查日志 ${LAAP_ROOT}/laap-nas.log"
exit 1
