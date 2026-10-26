"""A small robots.txt reader.

Python's ``urllib.robotparser`` drops a ``User-agent`` group when a blank
line sits between the agent name and its rules (RFC 9309 says empty lines
are ignored). Town sites and BidNet both use that layout, so the allow and
disallow decisions live here instead.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


class RobotsPolicy:
    def __init__(self, groups: list[dict], allow_all: bool = False, deny_all: bool = False):
        self.groups = groups
        self.allow_all = allow_all
        self.deny_all = deny_all

    @classmethod
    def allow_everything(cls) -> "RobotsPolicy":
        return cls([], allow_all=True)

    @classmethod
    def deny_everything(cls) -> "RobotsPolicy":
        return cls([], deny_all=True)

    def crawl_delay(self, user_agent: str) -> int | None:
        group = self._group_for(user_agent)
        if not group:
            return None
        return group.get("delay")

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if self.deny_all:
            return False
        if self.allow_all:
            return True
        group = self._group_for(user_agent)
        if not group:
            return True
        path = _match_path(url)
        best_len = -1
        best_allow = True
        for rule in group["rules"]:
            if rule["pattern"].search(path):
                length = rule["length"]
                if length > best_len or (length == best_len and rule["allow"] and not best_allow):
                    best_len = length
                    best_allow = rule["allow"]
        return best_allow

    def _group_for(self, user_agent: str) -> dict | None:
        product = _product_token(user_agent)
        best = None
        best_len = -1
        for group in self.groups:
            for agent in group["agents"]:
                if agent == "*":
                    match_len = 0
                elif product.startswith(agent):
                    match_len = len(agent)
                else:
                    continue
                if match_len > best_len:
                    best = group
                    best_len = match_len
        return best


def parse_robots(text: str) -> RobotsPolicy:
    groups: list[dict] = []
    current = None

    def start_group() -> dict:
        nonlocal current
        current = {"agents": [], "rules": [], "delay": None, "started": False}
        groups.append(current)
        return current

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = unquote(value.strip())
        if key == "user-agent":
            if current is None or current["started"]:
                start_group()
            current["agents"].append(value.lower())
        elif current is None:
            continue
        elif key == "disallow":
            current["started"] = True
            if value:
                current["rules"].append(_rule(value, allow=False))
        elif key == "allow":
            current["started"] = True
            current["rules"].append(_rule(value, allow=True))
        elif key == "crawl-delay":
            current["started"] = True
            if value.isdigit():
                current["delay"] = int(value)
    return RobotsPolicy(groups)


def _product_token(user_agent: str) -> str:
    token = user_agent.strip().split("/", 1)[0].split(None, 1)[0]
    return token.lower()


def _rule(path: str, allow: bool) -> dict:
    end = path.endswith("$")
    body = path[:-1] if end else path
    pieces = []
    for char in body:
        pieces.append(".*" if char == "*" else re.escape(char))
    expression = "".join(pieces)
    if end:
        expression += "$"
    return {
        "allow": allow,
        "length": len(path),
        "pattern": re.compile(expression),
    }


def _match_path(url: str) -> str:
    parts = urlsplit(url)
    path = unquote(parts.path or "/")
    if parts.query:
        return f"{path}?{parts.query}"
    return path
