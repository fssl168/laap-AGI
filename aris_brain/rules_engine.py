"""
Aris 规则引擎 — 规则引擎 (R11 拆分)
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

# ─── 规则引擎 (自原 aris_rules_engine.py 拆分) ────────────
from .rules_defs import (
    Rule, RuleStep, ToolRegistry, _validate_shell_cmd,
    _ALLOWED_CMD_TOKENS, _AUTO_EXEC_TOKENS, _DANGEROUS_CMD_PATTERNS,
    DEFAULT_RULES,
)
from .rules_tools import register_default_tools

class RulesEngine:
    """规则引擎 — 匹配输入→执行步骤→输出结果。"""

    # 命令执行需经使用人授权 (pending 状态, 120 秒有效)
    AUTH_TIMEOUT_SECONDS = 120
    CONFIRM_WORDS = ("确认", "同意", "执行", "可以", "好的", "是", "yes", "ok", "y", "确认执行", "同意执行", "批准")
    REJECT_WORDS = ("取消", "不要", "算了", "拒绝", "不执行", "停止", "no", "n", "不用", "别")

    def __init__(self):
        self.rules: List[Rule] = []
        self.tools = ToolRegistry()
        self._pending_cmd: Optional[Dict[str, Any]] = None  # {cmd, ts}
        register_default_tools(self.tools)
        self.rules = list(DEFAULT_RULES)

    def extract_intent(self, text: str) -> Dict[str, Any]:
        """从文本提取结构化意图。
        
        使用 aris_lm_v5.py 的NLP管线（如果可用），
        否则回退到关键词匹配。
        """
        # try:
        #     from aris_lm_v5 import ChineseTokenizer, DependencyParser, SemanticRoleLabeler
        #     # 完整的NLP管线
        #     tokens = tokenizer.tokenize(text)
        #     deps = parser.parse(tokens)
        #     srl = labeler.label(tokens, deps)
        #     return {"tokens": tokens, "deps": deps, "srl": srl, "raw": text}
        # except:
        #     pass
        
        # 回退: 关键词+正则提取
        intent = {"raw": text, "action": "unknown", "target": "", "params": {}}
        
        # 提取路径参数 (支持相对路径和绝对路径)
        path_match = re.search(r'[DCETdce]:[\\/][a-zA-Z0-9_\\/\.\-]+|[a-zA-Z0-9_\-]+\.(py|rs|md|txt|json|yaml|toml|bat|sh)|[a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+', text)
        if path_match:
            intent["params"]["path"] = path_match.group()
        
        # 提取命令参数 (在"运行"/"执行"/"run"之后的内容), 需通过安全校验
        for prefix in ["运行", "执行", "run", "execute"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                raw_cmd = text[idx:].strip()[:100]
                ok, reject_reason = _validate_shell_cmd(raw_cmd)
                if ok:
                    intent["params"]["cmd"] = raw_cmd
                else:
                    # 记录拒绝原因, 用于向使用人明确说明
                    intent["params"]["cmd_rejected"] = reject_reason
                break
        
        # 提取搜索查询
        for prefix in ["搜索", "搜一下", "找", "查找", "search", "find"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                intent["params"]["query"] = text[idx:].strip()[:50]
                break
        
        # 回忆类问题也提取 query (整句作查询词, 语义检索需要完整上下文)
        # 注意: 与搜索前缀不同, 回忆类提取的是整句去掉套话后的线索,
        #       保证 "你记得我刚才语音里说了什么吗?关于测试的" 能命中"测试"记忆。
        _RECALL_PREFIXES = ("我记得我", "记得我说", "我说过什么", "我之前说过", "我以前说",
                            "说了什么", "我刚才说", "回忆", "想起", "还记得", "记得我",
                            "我记得", "跟你说了", "跟你说过", "告诉你过", "跟你讲过",
                            "查询一下记忆中", "查询一下记忆", "查一下记忆中", "查一下记忆",
                            "读取记忆里", "读取记忆中", "读取记忆", "记忆中", "记忆里",
                            "从记忆里", "从记忆中", "从记忆")
        _RECALL_JUNK = ("吗？", "吗?", "吗", "呢", "什么", "哪些", "哪句话", "哪",
                        "？", "?", "一下", "再", "还", "过", "的事情", "的事", "然后")
        if "query" not in intent.get("params", {}):
            for prefix in _RECALL_PREFIXES:
                if prefix in text:
                    idx = text.index(prefix) + len(prefix)
                    rest = text[idx:].strip()
                    # 口语填充/疑问词清理 (前缀切点后, 疑问词可能在开头或结尾)
                    for junk in _RECALL_JUNK:
                        if rest.startswith(junk):
                            rest = rest[len(junk):].strip()
                            break
                    for junk in _RECALL_JUNK:
                        if rest.endswith(junk):
                            rest = rest[: -len(junk)].strip()
                            break
                    # 清理后无实质内容 → 用整句, 让语义检索自己找
                    # 若 rest 只剩套话(如"之前跟你说过") 也回退整句, 避免丢关键线索
                    _PURE_JUNK = ("之前跟你说过", "跟你说过", "跟你说了", "告诉你过",
                                  "跟你说", "跟你讲", "之前", "以前")
                    if len(rest) >= 2 and rest not in _PURE_JUNK:
                        intent["params"]["query"] = rest[:100]
                    else:
                        intent["params"]["query"] = text.strip()[:100]
                    break
            else:
                # 命中 recall_fact_rule 但没有任何前缀时 (如 "你记得...吗"),
                # 在 process() 中用整句兜底 (已有逻辑 1354 行附近)
                pass
        
        # 提取要记住的事实
        # 注意: 疑问句 (含 什么/哪些/有没有/是不是/吗?) 中的"记住"是回忆查询,
        #       不应提取为要写入的事实 (否则"我刚才让你记住的那句话是什么?"
        #       会被误存为记忆 → 记忆断层)。
        _RECALL_QUESTION_HINTS = ("什么", "哪些", "有没有", "是不是", "吗", "？", "?", "哪句话", "说了什么")
        for prefix in ["记住", "记下来", "别忘了", "记住我说"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                rest = text[idx:].strip()
                # 疑问句 → 是回忆请求, 不提取 fact (交给 recall_fact_rule)
                if any(h in rest for h in _RECALL_QUESTION_HINTS):
                    break
                intent["params"]["fact"] = rest[:500]
                break
        
        # 提取计划目标
        for prefix in ["生成计划", "制定计划", "帮我规划", "计划一下"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                intent["params"]["goal"] = text[idx:].strip()[:200]
                break

        # 提取要解释的代码文件
        for prefix in ["解释代码", "解释这个文件", "解释一下", "这段代码做什么", "explain code"]:
            if prefix in text:
                # path 已由上方通用正则提取，这里无需覆盖
                break

        # 提取对比的两个文件路径
        path_matches = re.findall(r'[DCETdce]:[\\/][a-zA-Z0-9_\\/\.\-]+|[a-zA-Z0-9_\-]+\.(py|rs|md|txt|json|yaml|toml|bat|sh)|[a-zA-Z0-9_\-/]+\.[a-zA-Z0-9]+', text)
        if len(path_matches) >= 2:
            intent["params"]["path_a"] = path_matches[0]
            intent["params"]["path_b"] = path_matches[1]

        # 提取 Python 代码
        for prefix in ["运行python", "执行python", "跑python", "run python", "execute python", "python:"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                intent["params"]["code"] = text[idx:].strip()[:2000]
                break

        # 提取写入文件的 path/content（格式：写文件 <path> <content>）
        for prefix in ["写入文件", "写文件", "创建文件", "保存到", "write file", "save to file"]:
            if prefix in text:
                idx = text.index(prefix) + len(prefix)
                rest = text[idx:].strip()
                parts = rest.split(None, 1)
                if len(parts) >= 1:
                    intent["params"]["path"] = parts[0]
                if len(parts) >= 2:
                    intent["params"]["content"] = parts[1]
                break

        # 列出记忆数量限制
        intent["params"]["limit"] = 10
        for prefix in ["列出记忆", "我的记忆", "最近记忆", "list memories", "show memories"]:
            if prefix in text:
                # 简单支持“列出10条记忆”这类表达
                m = re.search(r'(\d+)\s*条', text)
                if m:
                    intent["params"]["limit"] = int(m.group(1))
                break

        # 默认用户名
        intent["params"]["user_name"] = "朋友"

        # ── Phase 2 新增: 量化交易参数提取 (方案 v2.0 §4.3) ──
        # 从用户输入提取 symbol(股票代码/名称) + action(买/卖) + qty(股数),
        # 供 pt_decide/pt_execute/pt_close 规则使用。
        # 提取规则: A股6位代码 / "xx股票" / 常见名称; 不匹配时留空由工具兜底。
        # 股票代码 (6 位数字, 排除纯日期等; 用 (?<!\d)(?!\d) 边界,
        # 兼容中文上下文如 "600519要不要买")
        m = re.search(r'(?<!\d)(6\d{5}|0\d{5}|3\d{5})(?!\d)', text)
        if m:
            intent["params"]["symbol"] = m.group(1)
        # 买卖意图 (仅在明确动词时提取, 避免误判)
        action = ""
        if re.search(r'买(入)?\s*(不)?\s*$|买入|要不要买|值得买|可以买|买吧|就买', text):
            action = "buy"
        elif re.search(r'卖出|要不要卖|值得卖|卖吧|平仓|清仓', text):
            action = "sell"
        if action:
            intent["params"]["action"] = action
        # 股数 (如 "100股" / "买100")
        m = re.search(r'(\d+)\s*(股|手)', text)
        if m:
            qty = int(m.group(1)) * (100 if m.group(2) == "手" else 1)
            intent["params"]["qty"] = qty
        # 确认词 (pt_execute 二次确认)
        if re.search(r'确认执行|确认下单|确认平仓|下单吧|就买|买吧|执行', text):
            intent["params"]["confirm_word"] = "确认执行"

        return intent

    # ─── 规则匹配 ────────────────────────────────────────

    def match(self, text: str) -> Optional[tuple[Rule, float]]:
        """找最佳匹配规则。"""
        best_rule, best_score = None, 0.0
        for rule in self.rules:
            score = rule.match_score(text)
            if score > best_score and score >= rule.min_confidence:
                best_rule, best_score = rule, score
        return (best_rule, best_score) if best_rule else None

    # ─── 执行 ────────────────────────────────────────────

    def execute(self, rule: Rule, intent: Dict[str, Any]) -> Dict[str, str]:
        """执行规则的所有步骤。"""
        context = {}
        
        # 合并参数
        params = intent.get("params", {})
        
        for step in rule.steps:
            # 展开参数模板
            step_params = {}
            for k, v in step.params.items():
                if isinstance(v, str):
                    # 模板替换 {key} → context里的值或params里的值
                    for ctx_key in list(context.keys()) + list(params.keys()):
                        v = v.replace(f"{{{ctx_key}}}", str(context.get(ctx_key, params.get(ctx_key, v))))
                step_params[k] = v
            
            # 调用工具
            tool_fn = self.tools.get(step.tool)
            if tool_fn is None:
                context[step.output_key or "error"] = f"[未知工具: {step.tool}]"
                continue
            
            try:
                result = tool_fn(**step_params)
                key = step.output_key or step.tool
                context[key] = str(result)[:3000]
            except Exception as e:
                context[step.output_key or "error"] = f"[执行失败] {e}"
        
        return context

    # ─── 输出装配 ────────────────────────────────────────

    def render(self, rule: Rule, context: Dict[str, str]) -> str:
        """渲染输出。"""
        if rule.output_template:
            try:
                return rule.output_template.format(**context)
            except KeyError as e:
                return f"[模板渲染失败: 缺少 {e}]"
        
        # 默认: 拼接所有输出
        parts = []
        for k, v in context.items():
            if v and len(v) > 10:
                parts.append(f"[{k}]\n{v}")
        return "\n\n".join(parts) if parts else "[无输出]"

    # ─── 命令授权 ────────────────────────────────────────

    def _check_auth_expired(self) -> bool:
        """pending 授权是否已过期。过期则清除并返回 True。"""
        if self._pending_cmd is None:
            return False
        age = time.time() - self._pending_cmd.get("ts", 0)
        if age > self.AUTH_TIMEOUT_SECONDS:
            self._pending_cmd = None
            return True
        return False

    def _handle_pending(self, text: str) -> Optional[Dict[str, Any]]:
        """有待授权命令时, 处理用户确认/拒绝。返回处理结果或 None(非确认/拒绝)。"""
        if self._pending_cmd is None:
            return None
        if self._check_auth_expired():
            return {
                "matched": True,
                "rule": "run_command",
                "intent": "run_command",
                "confidence": 0.0,
                "output": "[授权已过期] 命令未执行, 请重新发起命令请求。",
                "latency_ms": 0,
            }
        t = text.strip().lower().strip("，。！？!? .,;；")
        cmd = self._pending_cmd.get("cmd", "")
        # 确认: 单独确认词, 或以确认词开头
        for w in self.CONFIRM_WORDS:
            if t == w or t.startswith(w):
                self._pending_cmd = None
                # 确认执行前再次过安全校验 (纵深防御: 即使 pending 被污染也拦截)
                ok, reason = _validate_shell_cmd(cmd)
                if not ok:
                    return {
                        "matched": True,
                        "rule": "run_command",
                        "intent": "run_command",
                        "confidence": 0.0,
                        "output": f"[安全拦截] 命令未执行: {reason}",
                        "latency_ms": 0,
                    }
                try:
                    tool = self.tools.get("terminal")
                    out = tool(cmd=cmd)
                except Exception as e:
                    out = f"[执行失败] {e}"
                return {
                    "matched": True,
                    "rule": "run_command",
                    "intent": "run_command",
                    "confidence": 0.0,
                    "output": f"✓ 已获得授权, 执行命令: {cmd}\n{out}",
                    "latency_ms": 0,
                }
        # 拒绝
        for w in self.REJECT_WORDS:
            if t == w or t.startswith(w):
                self._pending_cmd = None
                return {
                    "matched": True,
                    "rule": "run_command",
                    "intent": "run_command",
                    "confidence": 0.0,
                    "output": f"[已取消] 命令未执行: {cmd}",
                    "latency_ms": 0,
                }
        # 其他内容 → 仍在等待授权
        return {
            "matched": True,
            "rule": "run_command",
            "intent": "run_command",
            "confidence": 0.0,
            "output": (
                f"[待授权] 有一命令等待你的确认: `{cmd}`\n"
                f"回复「确认」执行, 或「取消」放弃 (有效期 {self.AUTH_TIMEOUT_SECONDS} 秒)。"
            ),
            "latency_ms": 0,
        }

    def _handle_command(
        self,
        cmd: str,
        intent: Dict[str, Any],
        t0: float,
        rule: Optional["Rule"] = None,
        score: float = 0.0,
    ) -> Dict[str, Any]:
        """命令分级处理:
        1. 危险命令 (命中 _DANGEROUS_CMD_PATTERNS)  → 硬拦截
        2. 白名单 + 免授权 token (git/查询/只读)   → 直接执行
        3. 白名单 + 其他 token (安装/构建/写/网络)  → 需授权确认
        4. 白名单外                                  → 拒绝
        """
        rule_name = rule.name if rule else "run_command"
        rule_intent = rule.intent if rule else "run_command"

        ok, reason = _validate_shell_cmd(cmd)
        if not ok:
            return {
                "matched": True,
                "rule": rule_name,
                "intent": rule_intent,
                "confidence": round(score, 3),
                "output": f"[安全拦截] 命令未执行: {reason}",
                "latency_ms": round((time.time() - t0) * 1000, 1),
            }

        first = cmd.strip().split()[0].lower()

        # 免授权命令: 常规开发/查询操作, 直接执行
        if first in _AUTO_EXEC_TOKENS:
            try:
                tool = self.tools.get("terminal")
                out = tool(cmd=cmd)
            except Exception as e:
                out = f"[执行失败] {e}"
            return {
                "matched": True,
                "rule": rule_name,
                "intent": rule_intent,
                "confidence": round(score, 3),
                "output": f"✓ 执行: {cmd}\n{out}",
                "latency_ms": round((time.time() - t0) * 1000, 1),
            }

        # 有副作用命令: 进入待授权
        self._pending_cmd = {"cmd": cmd, "ts": time.time()}
        return {
            "matched": True,
            "rule": rule_name,
            "intent": rule_intent,
            "confidence": round(score, 3),
            "output": (
                f"[需要授权] 你请求执行命令: `{cmd}`\n"
                f"⚠️ 命令将在我确认后执行。回复「确认」授权, 或「取消」拒绝。"
            ),
            "latency_ms": round((time.time() - t0) * 1000, 1),
        }

    # ─── 一站式入口 ──────────────────────────────────────

    def process(self, text: str) -> Dict[str, Any]:
        """处理一条输入：意图提取→规则匹配→执行→输出。

        安全策略: 命令类规则 (run_command) 不直接执行,
        先进入待授权状态, 经使用人确认后才执行。
        """
        t0 = time.time()

        # 待授权命令优先处理 (确认/拒绝/等待)
        pending_result = self._handle_pending(text)
        if pending_result is not None:
            return pending_result

        intent = self.extract_intent(text)

        # 命令意图优先: 若提取到有效命令, 直接走命令流程 (避免被其他规则抢走,
        # 如 "运行 git status" 不应被 query_status 规则截胡)
        cmd = intent.get("params", {}).get("cmd", "")
        if cmd:
            return self._handle_command(cmd, intent, t0)

        match_result = self.match(text)

        if match_result is None:
            return {
                "matched": False,
                "output": f"[未匹配到规则] 输入: {text[:60]}",
                "latency_ms": round((time.time() - t0) * 1000, 1),
            }

        rule, score = match_result

        # 记忆写入误判防护: 命中 remember_fact_rule 但 fact 为空 (疑问句回忆请求)
        # 时, 重定向到 recall_fact_rule — 防止"我刚才让你记住的那句话是什么?"
        # 被当作写入指令 (记忆断层根因之一)。
        if rule.name == "remember_fact_rule" and not intent.get("params", {}).get("fact", ""):
            recall_rule = next((r for r in self.rules if r.name == "recall_fact_rule"), None)
            if recall_rule is not None:
                rule = recall_rule
                score = rule.match_score(text)
                # 用整条输入作为回忆查询词 (疑问句的"记住"位置之前的内容才是线索)
                intent.setdefault("params", {})["query"] = text.strip()[:100]

        # 命令类规则: 分级处理 (免授权直接执行 / 需授权等待确认)
        if rule.name == "run_command":
            params = intent.get("params", {})
            cmd = params.get("cmd", "")
            reject_reason = params.get("cmd_rejected", "")
            if reject_reason:
                return {
                    "matched": True,
                    "rule": rule.name,
                    "intent": rule.intent,
                    "confidence": round(score, 3),
                    "output": f"[安全拦截] 命令未执行: {reject_reason}",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }
            if not cmd:
                return {
                    "matched": True,
                    "rule": rule.name,
                    "intent": rule.intent,
                    "confidence": round(score, 3),
                    "output": "[无法执行] 未能从输入中解析出有效命令 (命令需通过安全校验)。",
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }
            return self._handle_command(cmd, intent, t0, rule, score)

        context = self.execute(rule, intent)
        output = self.render(rule, context)

        return {
            "matched": True,
            "rule": rule.name,
            "intent": rule.intent,
            "confidence": round(score, 3),
            "output": output,
            "latency_ms": round((time.time() - t0) * 1000, 1),
        }


