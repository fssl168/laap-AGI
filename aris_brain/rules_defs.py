"""
Aris 规则引擎 — 规则定义表 (R11 拆分)
====================================
原 aris_rules_engine.py (1503 行) 拆分出的子模块之一。
完整拆分: rules_defs.py(定义表) / rules_tools.py(工具) /
          rules_engine.py(引擎) / rules_api.py(门面) /
          aris_rules_engine.py(薄门面, 保持既有导入零破坏)。
"""

import sys, os, json, re, time, subprocess, logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field

from laap_brain.config import LAAP_ROOT
_root = str(LAAP_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

logger = logging.getLogger("aris.rules")

# ─── 工具注册表 ──────────────────────────────────────────
class ToolRegistry:
    """注册可用的工具函数。所有工具是纯Python函数，不走LLM。"""

    def __init__(self):
        self._tools: Dict[str, Callable] = {}

    def register(self, name: str, fn: Callable, desc: str = ""):
        self._tools[name] = fn

    def get(self, name: str) -> Optional[Callable]:
        return self._tools.get(name)

    def list(self) -> List[str]:
        return list(self._tools.keys())




# ─── 命令安全校验 ──────────────────────────────────────
# 允许执行的安全命令首 token (白名单)
_ALLOWED_CMD_TOKENS = frozenset([
    "ls", "cat", "pwd", "echo", "date", "whoami", "hostname", "uname",
    "uptime", "df", "free", "ps", "top", "head", "tail", "wc", "grep",
    "find", "tree", "du", "stat", "file", "type", "which",
    "python", "python3", "pip", "pip3", "git", "curl", "wget",
    "docker", "systemctl", "journalctl",
    "nproc", "lscpu", "lsblk", "ifconfig", "ip", "ss", "netstat",
    "ping", "nslookup", "dig",
    "tar", "zip", "unzip", "gzip", "sed", "awk", "sort", "uniq", "cut",
    "tr", "xargs", "make", "gcc", "g++", "cmake",
    "node", "npm", "npx", "yarn", "pnpm",
])

# 免授权命令首 token: 常规开发/查询操作, 直接执行无需使用人确认。
# 分级策略:
#   - 危险命令 (命中 _DANGEROUS_CMD_PATTERNS)  → 硬拦截
#   - 白名单 + 免授权 token (本集合)          → 直接执行
#   - 白名单 + 其他 token (有副作用: 安装/构建/写文件/网络) → 需授权
#   - 白名单外                                  → 拒绝
_AUTO_EXEC_TOKENS = frozenset([
    # 版本控制 (常规开发操作)
    "git",
    # 只读查询/查看
    "ls", "cat", "pwd", "echo", "date", "whoami", "hostname", "uname",
    "uptime", "df", "free", "ps", "top", "head", "tail", "wc", "grep",
    "find", "tree", "du", "stat", "file", "type", "which",
    "nproc", "lscpu", "lsblk", "ifconfig", "ip", "ss", "netstat",
    "ping", "nslookup", "dig",
    # 文本处理 (只读/纯文本变换)
    "sort", "uniq", "cut", "tr", "awk",
])

# 危险模式 (正则, 命中即拒绝)
# 注意: 删除/移动类 (rm/mv) 仅拦截"独立命令位置" (行首 或 ; && | 之后),
#       不拦截 git rm / git mv 这类 git 子命令 (常规开发操作)。
_CMD_BOUNDARY = r"(^|[;&|]\s*|&&\s*|\|\|\s*)"
_DANGEROUS_CMD_PATTERNS = [
    _CMD_BOUNDARY + r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\b",  # rm -rf / rm -fr / rm -r
    _CMD_BOUNDARY + r"rm\b",                            # 独立 rm (删除操作一律拒绝)
    _CMD_BOUNDARY + r"mv\b",                            # 独立 mv (移动/覆盖文件)
    _CMD_BOUNDARY + r"dd\b",                            # dd 磁盘级操作
    _CMD_BOUNDARY + r"mkfs",                            # 格式化
    _CMD_BOUNDARY + r"fdisk\b",                         # 分区
    r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b", r"\bhalt\b", r"\binit\b",
    _CMD_BOUNDARY + r"kill\b", _CMD_BOUNDARY + r"pkill\b", _CMD_BOUNDARY + r"killall\b",
    _CMD_BOUNDARY + r"chmod\b", _CMD_BOUNDARY + r"chown\b", _CMD_BOUNDARY + r"chgrp\b",
    _CMD_BOUNDARY + r"sudo\b", _CMD_BOUNDARY + r"su\b", r"\bpasswd\b",
    _CMD_BOUNDARY + r"useradd\b", _CMD_BOUNDARY + r"userdel\b",
    _CMD_BOUNDARY + r"groupadd\b", _CMD_BOUNDARY + r"groupdel\b",
    _CMD_BOUNDARY + r"iptables\b", _CMD_BOUNDARY + r"nft\b", _CMD_BOUNDARY + r"ufw\b",
    _CMD_BOUNDARY + r"mount\b", _CMD_BOUNDARY + r"umount\b",
    _CMD_BOUNDARY + r"swapoff\b", _CMD_BOUNDARY + r"swapon\b",
    _CMD_BOUNDARY + r"parted\b", _CMD_BOUNDARY + r"cryptsetup\b",
    r"\bdocker\s+(rm|rmi|kill|stop|compose\s+down)\b",
    r"\bsystemctl\s+(stop|disable|mask|reboot|poweroff)\b",
    r"\bmake\s+install\b",
    r":\(\)\s*\{",                          # fork bomb
    r"\|\s*(sh|bash|zsh)\b",               # 管道进 shell
    r">\s*/dev/sd",                          # 写裸盘
    r"\bcurl\b[^|]*\|\s*(sh|bash)",        # curl | sh
    r"\bwget\b[^|]*\|\s*(sh|bash)",        # wget | sh
    r"(^|[;&|]\s*)\brm\s+-rf\s+/",          # 根目录删除 (兜底)
    r"(^|\s)(/etc/shadow|/etc/gshadow|/etc/passwd|/etc/sudoers|/etc/ssh/|/root/|\.ssh/|\.aws/|\.gnupg/|id_rsa|id_ed25519|\.pem\b|\.key\b|credentials)",  # 系统敏感文件/密钥
    # 代码执行上下文 (os.system / subprocess 等绕过检测的路径)
    r"os\.system\s*\(",
    r"os\.popen\s*\(",
    r"subprocess\s*\.\s*(run|call|Popen|check_output|check_call)\s*\(",
    r"eval\s*\(|exec\s*\(|__import__\s*\(",
]


# ─── 命令安全校验函数 ──────────────────────────────────
def _validate_shell_cmd(cmd: str) -> tuple[bool, str]:
    """校验 shell 命令是否安全。返回 (是否允许, 拒绝原因)。

    规则:
      1. 空命令 / 占位符残留 / 过长 → 拒绝
      2. 危险模式 (删除/格式化/关机/提权/管道注入等) → 拒绝
      3. 首 token 不在白名单 → 拒绝 (默认拒绝)
    """
    if not cmd or not cmd.strip():
        return False, "空命令"
    if "{" in cmd or "}" in cmd:
        return False, "命令含未解析占位符"
    if len(cmd) > 200:
        return False, "命令过长 (>200字符)"
    for pat in _DANGEROUS_CMD_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return False, f"命中危险模式: {pat}"
    first = cmd.strip().split()[0].lower()
    # 允许路径形式的命令 (如 ./script.sh) 仅限可执行文件且非危险脚本
    if first not in _ALLOWED_CMD_TOKENS:
        if first.startswith(("./", "../")) or first.endswith((".sh", ".py")):
            return False, f"脚本执行需显式白名单: {first}"
        return False, f"命令不在白名单: {first}"
    return True, ""


# ─── 规则定义 ────────────────────────────────────────────

@dataclass
class RuleStep:
    """规则的一个执行步骤。"""
    tool: str           # 工具名
    params: Dict        # 参数
    output_key: str = ""  # 结果存到上下文的哪个key
    condition: str = ""   # 可选: 执行条件 (python表达式)


@dataclass
class Rule:
    """一条完整规则 — 模式→意图→步骤→输出。"""
    name: str
    patterns: List[str]          # 触发关键词/模式
    intent: str                  # 意图标识
    description: str             # 描述
    steps: List[RuleStep]        # 执行步骤
    output_template: str = ""    # 输出模板 (格式字符串)
    min_confidence: float = 0.08  # 最低匹配置信度

    def match_score(self, text: str) -> float:
        """计算文本匹配这条规则的分数。"""
        text_lower = text.lower()
        matched = [p for p in self.patterns if p.lower() in text_lower]
        if not matched:
            return 0.0
        # 确保任何匹配至少有一个基础分
        base = 0.08
        long_matches = [p for p in matched if len(p) >= 2]
        short_bonus = 0.05 if any(len(p) < 2 for p in matched) and long_matches else 0.0
        ratio = len(long_matches or matched) / max(len(self.patterns), 1)
        matched_len = sum(len(p) for p in matched)
        density = matched_len / max(len(text), 1)
        return max(base, ratio * 0.4 + min(density, 1.0) * 0.4 + short_bonus)



# ─── 内置规则数据表 (自原 _register_default_rules 提取) ───────
DEFAULT_RULES: List[Rule] = [
            Rule(
                name="bootstrap_laap_rule",
                patterns=["接入laap", "全面接入", "唤醒aris", "唤醒 aris", "bootstrap laap", "awaken aris"],
                intent="bootstrap_laap",
                description="全面接入LAAP，触发Aris觉醒仪式",
                steps=[
                    RuleStep(tool="bootstrap_laap", params={"user_name": "{user_name}"}, output_key="ceremony"),
                ],
                output_template="{ceremony}",
                min_confidence=0.05,
            ),
            Rule(
                name="check_status",
                patterns=["状态", "情况", "你在干嘛", "在做什么", "你现在如何", "status", "health", "心跳", "psi状态", "qre状态", "今天怎么样", "今天感觉", "感觉怎么样", "你怎么样", "过得怎么样", "最近怎么样", "how are you", "how do you feel"],
                intent="query_status",
                description="查询Aris当前认知状态",
                steps=[
                    RuleStep(tool="status_narrate", params={}, output_key="narrate"),
                ],
                output_template="{narrate}",
            ),
            Rule(
                name="generate_paper_rule",
                patterns=["写论文", "生成论文", "论文综述", "写文章", "综述", "文章生成", "论文"],
                intent="generate_paper",
                description="生成零LLM论文",
                steps=[
                    RuleStep(tool="read_qre", params={}, output_key="qre_state"),
                    RuleStep(tool="generate_paper", params={"target_chars": 2000}, output_key="paper"),
                ],
                output_template="{paper}",
            ),
            Rule(
                name="self_intro_rule",
                patterns=["介绍你自己", "自我介绍", "你是谁", "你是谁呀", "介绍一下自己", "介绍一下你自己", "你是谁？", "你叫什么", "你叫什么名字", "tell me about yourself", "about you", "who are you", "introduce yourself"],
                intent="self_intro",
                description="Aris自我介绍",
                steps=[
                    RuleStep(tool="self_intro", params={}, output_key="intro"),
                ],
                output_template="{intro}",
                min_confidence=0.05,
            ),
            Rule(
                name="feeling_rule",
                patterns=["你的感受", "你感觉", "你觉得怎么样", "你现在感觉", "感受如何", "你的心情", "你心情", "感受", "现在的感受", "feel", "feeling", "how do you feel", "你现在怎么样", "你还好吗", "你还好么", "跟我说说", "说说你的"],
                intent="query_feelings",
                description="Aris现在的感受（口语化，适合语音）",
                steps=[
                    RuleStep(tool="feelings", params={}, output_key="feelings"),
                ],
                output_template="{feelings}",
                min_confidence=0.05,
            ),
            Rule(
                name="my_journey_rule",
                patterns=["你的历程", "你的经历", "最近发生", "最近的事情", "说说你自己", "你的故事", "回顾一下", "journey", "你的历史", "你怎么来的", "你最近在做什么", "最近在忙什么", "今天做了什么", "今天发生了什么", "最近的情况", "你的一天", "讲讲你的经历"],
                intent="my_journey",
                description="Aris回顾自己的历程",
                steps=[
                    RuleStep(tool="my_journey", params={}, output_key="journey"),
                ],
                output_template="{journey}",
                min_confidence=0.05,
            ),
            Rule(
                name="search_code",
                patterns=["搜索", "搜", "查找", "找一找", "在哪里", "search", "find", "grep", "关键"],
                intent="search_files",
                description="搜索代码或文件",
                steps=[
                    RuleStep(tool="search_files", params={"pattern": "{query}"}, output_key="results"),
                ],
                output_template="搜索结果:\n{results}",
            ),
            Rule(
                name="read_code",
                # 收窄触发词: 移除宽泛的"看/显示"(易误触发, 如"看法""显示状态"),
                # 仅保留明确"读文件/看代码"意图表达。
                patterns=["读取文件", "打开文件", "查看文件", "读文件", "读取", "看代码", "看下代码", "看文件", "查看代码", "read file", "read the file", "open file", "open the file", "cat ", "print file"],
                intent="read_file",
                description="读取文件内容",
                steps=[
                    RuleStep(tool="read_file", params={"path": "{path}"}, output_key="content"),
                ],
                output_template="{content}",
            ),
            Rule(
                name="list_files_rule",
                patterns=["列出目录", "有哪些文件", "显示目录", "目录列表", "dir"],
                intent="list_files",
                description="列出目录内容",
                steps=[
                    RuleStep(tool="list_files", params={"path": "{path}", "pattern": "{pattern}"}, output_key="files"),
                ],
                output_template="{files}",
            ),
            Rule(
                name="run_command",
                # 收窄触发词: 移除宽泛的"启动/编译/构建/start/build" (易误触发),
                # 仅保留明确命令意图表达。
                patterns=[
                    "运行命令", "执行命令", "帮我运行", "帮我执行", "请运行", "请执行",
                    "运行以下", "执行以下", "运行这个", "执行这个",
                    "run command", "run the command", "execute command",
                    "运行 ", "执行 ", "run ", "execute ",
                ],
                intent="run_command",
                description="执行shell命令 (经安全校验)",
                steps=[
                    RuleStep(tool="terminal", params={"cmd": "{cmd}"}, output_key="output"),
                ],
                output_template="{output}",
            ),
            Rule(
                name="remember_fact_rule",
                patterns=["记住", "记下来", "别忘了", "记住我说", "save memory"],
                intent="remember_fact",
                description="把事实保存到语义记忆",
                steps=[
                    RuleStep(tool="remember_fact", params={"fact": "{fact}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="recall_fact_rule",
                patterns=["回忆", "记得", "想起", "我之前说过", "我以前说", "recall memory", "记得我说", "我说过什么", "说了什么", "记得我", "还记得", "查询一下记忆", "查一下记忆", "记忆中", "读取记忆", "记忆里", "从记忆"],
                intent="recall_fact",
                description="从语义记忆召回相关事实",
                steps=[
                    RuleStep(tool="recall_fact", params={"query": "{query}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="analyze_project_rule",
                patterns=["分析项目", "项目结构", "代码统计", "项目概况", "analyze project", "project structure"],
                intent="analyze_project",
                description="分析项目结构和代码量",
                steps=[
                    RuleStep(tool="analyze_project", params={"path": "{path}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="summarize_file_rule",
                patterns=["总结文件", "摘要文件", "文件总结", "summarize file", "summarize", "文件概况"],
                intent="summarize_file",
                description="读取并摘要文件内容",
                steps=[
                    RuleStep(tool="summarize_file", params={"path": "{path}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="generate_plan_rule",
                patterns=["生成计划", "制定计划", "帮我规划", "计划一下", "generate plan", "make a plan"],
                intent="generate_plan",
                description="为目标生成结构化计划",
                steps=[
                    RuleStep(tool="generate_plan", params={"goal": "{goal}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="explain_code_rule",
                patterns=["解释代码", "解释这个文件", "解释一下", "这段代码做什么", "explain code", "what does this code do"],
                intent="explain_code",
                description="解释代码文件的作用",
                steps=[
                    RuleStep(tool="explain_code", params={"path": "{path}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="compare_files_rule",
                patterns=["比较文件", "对比文件", "差异", "diff", "compare files", "difference between"],
                intent="compare_files",
                description="比较两个文件的差异",
                steps=[
                    RuleStep(tool="compare_files", params={"path_a": "{path_a}", "path_b": "{path_b}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="run_python_rule",
                patterns=["运行python", "执行python", "跑python", "run python", "execute python", "python:"],
                intent="run_python",
                description="执行一段 Python 代码",
                steps=[
                    RuleStep(tool="run_python", params={"code": "{code}"}, output_key="result"),
                ],
                output_template="执行结果:\n{result}",
            ),
            Rule(
                name="write_file_rule",
                patterns=["写入文件", "写文件", "创建文件", "保存到", "write file", "save to file"],
                intent="write_file",
                description="将内容写入指定文件",
                steps=[
                    RuleStep(tool="write_file", params={"path": "{path}", "content": "{content}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="count_lines_rule",
                patterns=["统计行数", "代码行数", "多少行", "count lines", "line count"],
                intent="count_lines",
                description="统计项目代码行数",
                steps=[
                    RuleStep(tool="count_lines", params={"path": "{path}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="list_memories_rule",
                patterns=["列出记忆", "我的记忆", "最近记忆", "list memories", "show memories"],
                intent="list_memories",
                description="列出最近的语义记忆",
                steps=[
                    RuleStep(tool="list_memories", params={"limit": "{limit}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="ocr_document",
                patterns=["ocr", "OCR", "识别", "扫描", "提取文字", "图片文字", "read image", "read pdf"],
                intent="ocr",
                description="用OCR识别图片/PDF中的文字",
                steps=[
                    RuleStep(tool="terminal", params={"cmd": "cd /d/LAAP/aris_brain && python aris_ocr_bridge.py '{path}'"}, output_key="ocr_result"),
                ],
                output_template="{ocr_result}",
            ),
            # ─── Paper Trading 规则 (ARIS 接管) ───────────────────────
            Rule(
                name="pt_account_list",
                patterns=["paper交易账户", "纸面交易账户", "查看账户", "查账户", "账户列表", "account list", "list accounts"],
                intent="pt_account_list",
                description="列出所有paper_trading虚拟账户",
                steps=[
                    RuleStep(tool="pt_account_list", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_account_show",
                patterns=["查看账户详情", "账户状态", "account details", "account status"],
                intent="pt_account_show",
                description="查看指定账户的详细信息",
                steps=[
                    RuleStep(tool="pt_account_show", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_account_positions",
                patterns=["查看持仓", "我的持仓", "当前持仓", "positions", "持仓列表"],
                intent="pt_account_positions",
                description="查看账户当前持仓",
                steps=[
                    RuleStep(tool="pt_account_positions", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_strategy_list",
                patterns=["查看策略", "策略列表", "strategies", "strategy list", "有哪些策略"],
                intent="pt_strategy_list",
                description="列出所有paper_trading策略",
                steps=[
                    RuleStep(tool="pt_strategy_list", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_backtest_run",
                patterns=["跑回测", "跑个回测", "运行回测", "backtest", "回测分析", "测试策略"],
                intent="pt_backtest_run",
                description="对指定策略运行回测",
                steps=[
                    RuleStep(tool="pt_backtest_run", params={"strategy": "{strategy}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_risk_check",
                patterns=["风控检查", "检查风控", "风险检查", "risk check", "风控状态", "检查风险"],
                intent="pt_risk_check",
                description="检查paper_trading风控状态",
                steps=[
                    RuleStep(tool="pt_risk_check", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_performance",
                patterns=["绩效报告", "收益报告", "performance", "查看绩效", "盈亏情况"],
                intent="pt_performance",
                description="查看paper_trading绩效报告",
                steps=[
                    RuleStep(tool="pt_performance", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_health",
                patterns=["系统健康", "paper_trading健康", "health check", "系统状态"],
                intent="pt_health",
                description="检查paper_trading系统健康状态",
                steps=[
                    RuleStep(tool="pt_health", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── Phase 1 新增 (方案 v2.0 §4.2.1): 学习/识别类规则 ──
            Rule(
                name="pt_lessons_rule",
                patterns=["有什么教训", "学到什么", "交易教训", "吃过什么亏", "复盘教训", "lessons", "lesson"],
                intent="pt_lessons",
                description="查询交易教训（学习能力）",
                steps=[
                    RuleStep(tool="pt_lessons", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_signals_rule",
                patterns=["最近信号", "交易信号", "有什么信号", "信号列表", "列出信号", "最新信号", "signals"],
                intent="pt_signals",
                description="查询最近交易信号（识别能力）",
                steps=[
                    RuleStep(tool="pt_signals", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_net_value_rule",
                patterns=["赚了还是亏", "净值多少", "交易怎么样", "盈亏情况", "赚了没", "net value", "net_value", "净值"],
                intent="pt_net_value",
                description="查询净值与盈亏（识别能力）",
                steps=[
                    RuleStep(tool="pt_net_value", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_risk_events_rule",
                patterns=["风控拒绝", "被风控", "拒绝记录", "风控事件", "risk events", "risk_rejections"],
                intent="pt_risk_events",
                description="查询风控拒绝事件（识别能力）",
                steps=[
                    RuleStep(tool="pt_risk_events", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── 2026-08-16: 补缺规则——pt_orders/pt_trades/pt_evolve 原无规则 ──
            Rule(
                name="pt_orders_rule",
                patterns=["订单列表", "订单记录", "最近订单", "有订单吗", "orders", "order list"],
                intent="pt_orders",
                description="查询最近订单（识别能力）",
                steps=[
                    RuleStep(tool="pt_orders", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_trades_rule",
                patterns=["成交列表", "成交记录", "最近成交", "成交明细", "trades", "trade list", "成交情况", "交易记录", "交易明细", "我的交易"],
                intent="pt_trades",
                description="查询最近成交（识别能力）",
                steps=[
                    RuleStep(tool="pt_trades", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_evolve_rule",
                patterns=["演化记录", "演化历史", "进化记录", "参数演化", "evolutions", "演化明细"],
                intent="pt_evolve",
                description="查询演化记录（演化能力）",
                steps=[
                    RuleStep(tool="pt_evolve", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_portfolio_rule",
                patterns=["当前持仓", "我的持仓", "持仓情况", "有哪些持仓", "仓位", "positions", "portfolio"],
                intent="pt_portfolio",
                description="查询当前持仓（识别能力）",
                steps=[
                    RuleStep(tool="pt_account_positions", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── Phase 2 新增 (方案 v2.0 §4.3): 动作规则 (带审核/二次确认) ──
            Rule(
                name="pt_decide_rule",
                patterns=["要不要买", "要不要卖", "值得买吗", "值得卖吗", "该不该买", "该不该卖", "能买吗", "能卖吗", "可以买", "可以卖", "分析下买", "分析下卖", "卖出", "买入", "decide"],
                intent="pt_decide",
                description="交易决策建议（审核，不下单）",
                steps=[
                    RuleStep(tool="pt_decide", params={"symbol": "{symbol}", "action": "{action}", "qty": "{qty}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_execute_rule",
                patterns=["确认执行", "确认下单", "下单吧", "执行买入", "执行卖出"],
                intent="pt_execute",
                description="确认执行下单（需二次确认 + TradingSelf 审核）",
                steps=[
                    RuleStep(tool="pt_execute", params={"symbol": "{symbol}", "action": "{action}", "qty": "{qty}", "confirm_word": "{confirm_word}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_close_rule",
                patterns=["平仓", "清仓", "卖出持仓", "止盈", "止损", "close position"],
                intent="pt_close",
                description="平仓（需审核 + 确认）",
                steps=[
                    RuleStep(tool="pt_close", params={"symbol": "{symbol}", "qty": "{qty}", "confirm_word": "{confirm_word}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── Phase 3 新增 (方案 v2.0 §4.4): 管理闭环规则 ──
            Rule(
                name="pt_brief_rule",
                patterns=["今日交易简报", "今天交易怎么样", "今日复盘", "今天盈亏", "日报", "daily brief", "交易简报", "今天情况"],
                intent="pt_brief",
                description="每日交易简报（管理/报告能力）",
                steps=[
                    RuleStep(tool="pt_brief", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            Rule(
                name="pt_evolution_rule",
                patterns=["进化提案", "策略改进", "进化审计", "演化审计", "有哪些提案", "看下提案", "evolution audit", "进化治理"],
                intent="pt_evolution_audit",
                description="进化治理提案（管理/治理能力）",
                steps=[
                    RuleStep(tool="pt_evolution_audit", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── 2026-08-16: paper_trading 能力清单规则 ──
            # 只匹配带 paper_trading 前缀的查询 → 22 个交易工具清单。
            # （Hermes 全景清单由 hermes_capability_rule 负责，避免"所有功能"
            #  裸词误匹配——用户明确：本地按"所有功能"匹配同一个模板不正确）
            Rule(
                name="pt_capability_rule",
                patterns=["paper_trading 的所有功能", "paper_trading的所有功能", "paper_trading 所有功能", "paper_trading所有功能", "paper_trading 有哪些工具", "paper_trading有哪些工具", "paper_trading 功能列表", "paper_trading功能列表", "paper_trading 功能清单", "paper_trading功能清单", "paper_trading 有什么功能", "paper_trading有什么功能", "paper_trading 的工具", "paper_trading工具", "列出paper_trading", "列出 paper_trading", "paper_trading 能力", "paper_trading能力"],
                intent="pt_capability",
                description="paper_trading 22个交易工具清单",
                steps=[
                    RuleStep(tool="pt_health", params={}, output_key="health"),
                ],
                output_template="**📋 paper_trading 功能清单（22个交易工具）**\n\n| 类别 | 工具 | 功能 |\n|------|------|------|\n| **账户** | `pt_account_list` | 账户列表（单系统单账户） |\n| | `pt_account_show` | 账户详情 |\n| **持仓** | `pt_positions` | 查看持仓 |\n| | `pt_account_positions` | 持仓（规则别名） |\n| **策略** | `pt_strategies` | 策略列表 |\n| | `pt_strategy_list` | 策略（规则别名） |\n| **信号** | `pt_signals` | 信号列表（最近10条） |\n| **订单** | `pt_orders` | 订单列表（最近10条） |\n| **成交** | `pt_trades` | 成交列表（最近10笔） |\n| **绩效** | `pt_performance` | 绩效报告（净值/盈亏） |\n| | `pt_net_value` | 净值/盈亏摘要 |\n| **健康** | `pt_health` | 系统健康检查 |\n| **风控** | `pt_risk_check` | 风控检查 |\n| | `pt_risk_events` | 风控拒绝事件 |\n| **学习** | `pt_lessons` | 交易教训 |\n| **决策** | `pt_decide` | 交易决策建议（不下单） |\n| **执行** | `pt_execute` | 确认执行下单（需二次确认） |\n| **平仓** | `pt_close` | 平仓（需审核+确认） |\n| **简报** | `pt_brief` | 每日交易简报 |\n| **回测** | `pt_backtest_run` | 运行回测 |\n| **演化** | `pt_evolve` | 演化记录 |\n| | `pt_evolution_audit` | 进化治理提案 |\n\n---\n\n当前系统状态：\n{health}",
                min_confidence=0.05,
            ),
            # ── 2026-08-16: Hermes 全景能力清单规则 ──
            # 匹配"列出你所有功能"/"LAAP的所有功能"/"有哪些工具"等（无 paper_trading 前缀）→
            # 输出 Hermes Agent 全景清单（与 NAS 完全一致）。
            Rule(
                name="hermes_capability_rule",
                patterns=["列出你所有功能", "列出你的所有功能", "你所有功能", "你的所有功能", "LAAP的所有功能", "LAAP 的所有功能", "LAAP所有功能", "LAAP 所有功能", "LAAP的功能", "LAAP 的功能", "LAAP功能", "LAAP 功能", "LAAP有哪些功能", "LAAP 有哪些功能", "查LAAP", "有哪些工具", "有什么功能", "能力清单", "你会什么", "你能做什么", "功能列表", "功能清单", "支持什么", "你有什么工具"],
                intent="hermes_capability",
                description="Hermes Agent 全景能力清单（与 NAS 输出一致）",
                steps=[
                    RuleStep(tool="pt_health", params={}, output_key="health"),
                ],
                output_template="**📋 我的功能清单**\n\n---\n\n### 🤖 核心身份\n- **名称**: Hermes Agent\n- **模型**: agnes-2.5-flash\n- **提供商**: Agnes AI\n- **角色**: 枫哥的工作助理\n\n---\n\n### 🔧 可用工具 (24个)\n\n| 类别 | 工具 | 功能 |\n|------|------|------|\n| **对话** | `clarify` | 向用户提问获取澄清/反馈 |\n| **任务管理** | `todo` | 创建和管理待办事项列表 |\n| **记忆** | `memory` | 保存/检索持久化记忆 |\n| **搜索** | `session_search` | 搜索历史会话记录 |\n| **文件** | `read_file` | 读取文本文件 |\n| | `write_file` | 写入文件 |\n| | `patch` | 编辑文件（精确替换） |\n| | `search_files` | 搜索文件内容/名称 |\n| **代码** | `execute_code` | 执行 Python 脚本 |\n| **终端** | `terminal` | 执行 shell 命令 |\n| | `process` | 管理后台进程 |\n| **技能** | `skill_view` | 查看技能内容 |\n| | `skill_manage` | 创建/编辑/删除技能 |\n| | `skills_list` | 列出所有技能 |\n| **代理** | `delegate_task` | 委派子任务给子代理 |\n| **定时** | `cronjob` | 创建/管理定时任务 |\n| **视觉** | `vision_analyze` | 分析图片 |\n| **语音** | `text_to_speech` | 文字转语音 (TTS) |\n| **MCP** | `mcp__agnes_aigc_official__*` | Agnes AIGC 服务 (图像/视频/提示词) |\n\n---\n\n### 🔌 已集成服务\n\n| 服务 | 状态 | 功能 |\n|------|------|------|\n| **LAAP/Aris** | ✅ 已连接 | 认知引擎、记忆、交易决策 |\n| **paper_trading** | ✅ 已接入 | 22个交易工具（账户/持仓/净值/决策） |\n| **Agnes AIGC** | ✅ 已连接 | 图像生成、视频生成、提示词 |\n| **本地 LLM** | ✅ 可用 | MiniCPM5-1B-Q4_K_M (~10-17 t/s) |\n| **QQ消息** | ✅ 已连接 | 语音/文字双向互动 |\n\n---\n\n### 📊 已配置 Providers\n\n| Provider | 模型 | 用途 |\n|----------|------|------|\n| **agnes** | agnes-2.5-flash | 当前主力模型 |\n| **local** | MiniCPM5-1B | 快速本地问答 |\n| **nvidia** | deepseek-v4-flash | 云端推理 |\n| **laap** | laap-core | Aris 认知引擎 |\n\n---\n\n### 🎯 可执行任务类型\n\n1. **论文写作** - 科研论文、技术文档、格式排版\n2. **代码开发** - Python/Node.js/Shell 脚本、调试、重构\n3. **数据处理** - 数据分析、可视化、格式转换\n4. **系统运维** - Docker、服务部署、故障排查\n5. **AI 应用** - 模型测试、MCP 集成、工具链开发\n6. **研究分析** - 文献综述、实验设计、基准测试\n7. **日程管理** - 定时任务、提醒、自动化工作流\n\n---\n\n### 🚫 限制\n\n- ❌ 不能访问外部网络（部分 API 被墙）\n- ❌ 不能直接操作文件系统（需通过工具）\n- ❌ 不能自动执行危险命令（需确认）\n- ❌ 不能预测未来/提供投资建议\n\n---\n\n需要我演示哪个功能？👇",
                min_confidence=0.05,
            ),
            # ── 2026-08-16: 自选股列表规则 ──
            Rule(
                name="pt_watchlist_rule",
                patterns=["列出我的自选股", "我的自选股", "自选股列表", "自选股有哪些", "看看自选股", "watchlist", "自选股"],
                intent="pt_watchlist",
                description="列出我的自选股（读 .env STOCK_LIST）",
                steps=[
                    RuleStep(tool="pt_watchlist", params={}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── 2026-08-16: 个股资料规则 ──
            Rule(
                name="pt_profile_rule",
                patterns=["个股资料", "股票资料", "公司资料", "资料详情", "基本情况", "股票概况", "profile", "个股信息", "公司概况", "查一下资料", "查资料", "查股票", "看看资料", "介绍下", "介绍一下", "查查", "查一下"],
                intent="pt_profile",
                description="查询个股资料（行业/市值/主营等）",
                steps=[
                    RuleStep(tool="pt_profile", params={"symbol": "{symbol}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── 2026-08-16: 行业板块研报规则 ──
            Rule(
                name="pt_sector_reports_rule",
                patterns=["行业研报", "板块研报", "行业板块研报", "研报", "板块研报", "行业报告", "sector report", "行业分析报告", "板块报告"],
                intent="pt_sector_reports",
                description="查询行业/板块研报（自选股池聚合）",
                steps=[
                    RuleStep(tool="pt_sector_reports", params={"sector": "{sector}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
            # ── 2026-08-16: 个股新闻规则 ──
            Rule(
                name="pt_news_rule",
                # 引导词(查一下/查查/查下)必须与 profile 规则共存：带"新闻/消息/资讯"字样时
                # 本规则多个 pattern 命中 → score 更高胜出；纯资料查询(如"查一下 600519")
                # 仅引导词命中 → profile 因 patterns 更少 ratio 更高而胜出，两者互不破坏。
                patterns=["个股新闻", "最新新闻", "新闻", "消息", "资讯", "有什么新闻", "查新闻", "看看新闻", "查一下", "查查", "查下", "查一下新闻", "查查新闻", "查下新闻", "查一下消息", "查查消息", "查下消息", "有什么消息", "最新消息", "看看消息", "的新闻", "的消息", "news"],
                intent="pt_news",
                description="查询个股新闻（多源链）",
                steps=[
                    RuleStep(tool="pt_news", params={"symbol": "{symbol}"}, output_key="result"),
                ],
                output_template="{result}",
            ),
]


