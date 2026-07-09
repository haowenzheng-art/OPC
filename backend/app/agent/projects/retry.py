"""循环检测 — 替换固定重试上限作为主终止条件.

设计原则（来自用户原话）:
"重试的预算难道不是越多越好吗？重试说明任务难，简单任务自然agent不需要重试，
agent应该也不会做没意义的重试吧"

→ 不用固定 cap 作主终止。用循环检测:同样错误连出现两次 = LLM 卡住了 = 停。
不同错误每次 = 在推进 = 继续。高 cap (8) 作安全网,极少触发。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def normalize_error(err: str | None) -> str | None:
    """归一化错误字符串,用于循环检测.

    去掉路径、行号、时间戳、堆栈细节,只保留错误类型+核心信息。
    这样 "Cannot find module 'date-fns' at /path/a.ts:2" 和
    "Cannot find module 'date-fns' at /path/b.ts:5" 会被视为同一错误。
    """
    if not err:
        return None
    s = err.strip()
    # 去掉文件路径 (Windows + Unix)
    s = re.sub(r"[A-Za-z]:[\\\/][^\s'\"]+", "<path>", s)
    s = re.sub(r"(?:\./|\.\./|/)[\w\-./]+", "<path>", s)
    # 去掉行号信息 — :ln:col 形式 和 单 :ln 形式 和 "at line N"
    s = re.sub(r":\d+:\d+", "", s)
    s = re.sub(r":\d+(?=\s|,|'|\"|$)", "", s)
    s = re.sub(r"line \d+", "line <ln>", s, flags=re.IGNORECASE)
    # 去掉时间戳
    s = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", "<ts>", s)
    # 折叠空白
    s = re.sub(r"\s+", " ", s).strip()
    # 截断,避免超长错误比较整段
    return s[:500] if len(s) > 500 else s


@dataclass
class RetryState:
    """单个 agent 的重试状态机."""
    attempts: int = 0
    last_error: str | None = None  # normalized
    consecutive_same: int = 0
    stuck: bool = False
    history: list[str] = field(default_factory=list)  # 最近 3 条 normalized error

    def should_retry(self, new_error: str | None, cap: int = 8) -> bool:
        """决定是否应该重试.

        返回 True = 继续 retry; False = 停 (stuck 或 cap 触发)。
        new_error=None 表示本次成功,重置计数器(但仍返回 True,允许下次循环)。
        """
        if self.stuck:
            return False
        if self.attempts >= cap:
            self.stuck = True
            return False

        norm = normalize_error(new_error)
        if norm is None:
            # 成功一次,重置连续计数和 last_error
            self.consecutive_same = 0
            self.last_error = None
            return True

        if norm == self.last_error:
            self.consecutive_same += 1
            if self.consecutive_same >= 2:
                # 同样错误连出现两次 (1st set last_error, 2nd increments to 2) = LLM 卡住了
                self.stuck = True
                return False
        else:
            # 第一次见到这个错误
            self.consecutive_same = 1
            self.last_error = norm

        self.history.append(norm)
        self.history = self.history[-3:]
        return True

    def record_attempt(self) -> None:
        """每次发起一次生成就调一次."""
        self.attempts += 1

    def to_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "last_error": self.last_error,
            "consecutive_same": self.consecutive_same,
            "stuck": self.stuck,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RetryState":
        if not data:
            return cls()
        return cls(
            attempts=data.get("attempts", 0),
            last_error=data.get("last_error"),
            consecutive_same=data.get("consecutive_same", 0),
            stuck=data.get("stuck", False),
            history=data.get("history", []),
        )
