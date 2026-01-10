import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchRule:
    pattern: str
    _compiled: Optional[re.Pattern] = field(init=False)

    def __post_init__(self):
        self._compiled = re.compile(self.pattern, re.IGNORECASE)
