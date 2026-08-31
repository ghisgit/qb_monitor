import re
from dataclasses import dataclass


class TaskDeferredError(Exception):
    """任务暂时无法执行（如 AI 并发槽位占满）。

    由处理器抛出、worker 特判：按 INFO 记录且不计错误栈，触发标签保留，
    下个轮询周期自然重入队重试——不让位方阻塞其他处理器。
    """


@dataclass
class MatchRule:
    pattern: str
    compiled: re.Pattern | None = None

    def __post_init__(self):
        self.compiled = re.compile(self.pattern, re.IGNORECASE)

    def matches(self, text: str) -> bool:
        assert self.compiled is not None
        return bool(self.compiled.search(text))
