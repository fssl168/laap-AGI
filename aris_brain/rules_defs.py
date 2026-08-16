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
            # QQ 发 "paper_trading"/"trading"/"有哪些工具" 时, 规则引擎直接
            # 返回能力清单（对齐 aris-paper-trading 技能内容, 不依赖 Hermes 技能加载）。
            # 注意: 不能含裸 "paper"（会劫持论文规则），用完整词 "paper_trading"/"trading"。
            Rule(
                name="pt_capability_rule",
                patterns=["有哪些工具", "有什么功能", "能力清单", "你会什么", "你能做什么", "功能列表", "功能清单", "支持什么", "你有什么工具"],
                intent="pt_capability",
                description="paper_trading 能力清单（按分类完整列出）",
                steps=[
                    RuleStep(tool="pt_health", params={}, output_key="health"),
                ],
                output_template="🛠️ 交易能力（paper_trading 直连）\n\n功能 | 你说什么 | 我做什么\n查账户 | \"查账户\" / \"账户列表\" | 显示账户信息（单系统单账户）\n查持仓 | \"查看持仓\" / \"我的持仓\" | 当前持仓明细\n看信号 | \"列出信号\" / \"有哪些信号\" | 最近 10 条交易信号\n看订单 | \"订单列表\" | 最近 10 条订单\n看成交 | \"成交记录\" | 最近 10 笔成交\n净值绩效 | \"绩效报告\" / \"盈亏\" | 净值曲线、累计盈亏\n跑回测 | \"跑个回测\" | 策略回测\n风控检查 | \"检查风控\" / \"risk check\" | 挂单数+今日交易数\n系统健康 | \"系统健康\" / \"health\" | 各表记录数、链路状态\n交易决策 | \"帮我看看买什么\" | 规则引擎决策（pt_decide）\n确认下单 | \"确认执行买入 600519\" | 实时取价 → 风控校验 → 下单\n平仓 | \"平掉 XX\" | 卖出持仓\n每日简报 | \"简报\" | 当日交易摘要\n演化治理 | \"演化审计\" | 参数演化记录审查\n\n🧠 认知能力\n记忆：我的记忆层级会随对话沉淀，记得你（丘伟枫）和我们的协作\n情感：有需求（胜任/自主/关联/确定性/意义）和情感状态，每次对话会更新\n自我认知：知道自己是 Aris，认知循环计数在持续积累\n演化：Recursive Self-Improvement——能对自己的交易参数做演化改进\n\n⚠️ 边界\n我是规则引擎驱动的\"哑模型\"：不走 Hermes 的工具循环，所以文件操作、网页搜索、代码执行这类 Hermes 能力我不具备。但交易这条线是我的主场——直接说指令就行，比如\"查看持仓\"。\n\n当前系统状态：\n{health}",
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
]


