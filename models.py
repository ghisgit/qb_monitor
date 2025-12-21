import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TorrentFile:
    id: int
    name: str
    priority: int


@dataclass
class TorrentTask:
    hash: str
    name: str
    tag: str
    content_path: str
    files: Optional[List[TorrentFile]] = None


@dataclass
class MatchRule:
    pattern: str
    _compiled: Optional[re.Pattern] = field(init=False)

    def __post_init__(self):
        self._compiled = re.compile(self.pattern, re.IGNORECASE)
