#!/usr/bin/env python3
"""Content guardrails for autonomous posting.

Checks drafts against anti-patterns, length, and tone rules.
Configurable per-space via comms-config.yaml.
"""

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class GuardrailResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


class ContentGuardrails:
    def __init__(self, anti_patterns=None, max_length=280, max_exclamations=1,
                 max_capitals_ratio=0.3):
        self.anti_patterns = [p.lower() for p in (anti_patterns or [])]
        self.max_length = max_length
        self.max_exclamations = max_exclamations
        self.max_capitals_ratio = max_capitals_ratio

    def check(self, text: str) -> GuardrailResult:
        violations = []
        lower = text.lower()

        # Length check
        if len(text) > self.max_length:
            violations.append(f"Exceeds {self.max_length} chars ({len(text)})")

        # Anti-patterns
        for pattern in self.anti_patterns:
            if pattern in lower:
                violations.append(f"Anti-pattern: '{pattern}'")

        # Exclamation spam
        excl_count = text.count("!")
        if excl_count > self.max_exclamations:
            violations.append(f"Too many exclamations ({excl_count})")

        # ALL CAPS ratio
        letters = [c for c in text if c.isalpha()]
        if letters:
            caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if caps_ratio > self.max_capitals_ratio:
                violations.append(f"Too many capitals ({caps_ratio:.0%})")

        return GuardrailResult(passed=len(violations) == 0, violations=violations)
