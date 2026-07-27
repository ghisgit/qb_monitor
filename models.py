import re
from dataclasses import dataclass


@dataclass
class MatchRule:
    pattern: str
    compiled: re.Pattern | None = None

    def __post_init__(self):
        self.compiled = re.compile(self.pattern, re.IGNORECASE)

    def matches(self, text: str) -> bool:
        assert self.compiled is not None
        return bool(self.compiled.search(text))
