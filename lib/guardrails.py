# .datacore/modules/comms/lib/guardrails.py
"""Content guardrails — validates text against voice profile rules.

Used by both autonomous and human-approved pipelines.
Loads rules from voice.yaml or manual configuration.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional


# Claims of a pilot or deployment with an institution (MEM-18). No record of
# executed pilots exists in the system, so none of these can be verified by a
# machine: every one is stopped for a human, whatever the voice file says.
CLAIM_PATTERNS = [
    re.compile(r"\b(?:live|active|running|ongoing|successful|paid)\s+pilots?\b", re.I),
    re.compile(r"\bpilots?\s+(?:programme\s+|program\s+|project\s+)?(?:with|at|in)\s+(?:the\s+)?[A-Z]"),
    re.compile(r"\b(?:now\s+)?(?:deployed|live|rolled\s+out|running)\s+(?:at|in|with|across)\s+(?:the\s+)?[A-Z]"),
    re.compile(r"\bin\s+production\s+(?:with|at|in)\b", re.I),
    re.compile(r"\b(?:deployment|rollout)\s+(?:with|at)\s+(?:the\s+)?[A-Z]"),
]


def claim_violations(text: str) -> List[str]:
    """Unverifiable pilot/deployment claims in ``text`` (MEM-18)."""
    out = []
    for rx in CLAIM_PATTERNS:
        m = rx.search(text)
        if m:
            out.append(f"Unverified pilot/deployment claim: '{m.group(0)}' -- "
                       "a human must confirm it happened before this is published")
    return out


class ClaimNotVerified(ValueError):
    """Raised by a publisher when text claims a pilot or deployment (MEM-18)."""


@dataclass
class GuardrailResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


class ContentGuardrails:
    def __init__(
        self,
        anti_patterns: List[str] = None,
        promo_patterns: List[str] = None,
        max_length: int = 280,
        max_exclamations: int = 1,
        max_hashtags: int = 2,
        allow_emoji: bool = True,
    ):
        self.anti_patterns = anti_patterns or []
        self.promo_patterns = promo_patterns or []
        self.max_length = max_length
        self.max_exclamations = max_exclamations
        self.max_hashtags = max_hashtags
        self.allow_emoji = allow_emoji

    def check(self, text: str) -> GuardrailResult:
        violations = []

        # Length
        if len(text) > self.max_length:
            violations.append(
                f"Exceeds max length: {len(text)} > {self.max_length}"
            )

        # Anti-patterns (case-insensitive, word-boundary matching)
        for pattern in self.anti_patterns:
            regex = r'\b' + re.escape(pattern) + r'\b'
            if re.search(regex, text, re.IGNORECASE):
                violations.append(f"Anti-pattern matched: '{pattern}'")

        # Promo patterns (word-boundary matching)
        for pattern in self.promo_patterns:
            regex = r'\b' + re.escape(pattern) + r'\b'
            if re.search(regex, text, re.IGNORECASE):
                violations.append(f"Promotional language: '{pattern}'")

        # Exclamation marks
        if text.count('!') > self.max_exclamations:
            violations.append(
                f"Too many exclamation marks: {text.count('!')} > {self.max_exclamations}"
            )

        # Hashtags
        hashtag_count = len(re.findall(r'#\w+', text))
        if hashtag_count > self.max_hashtags:
            violations.append(
                f"Too many hashtags: {hashtag_count} > {self.max_hashtags}"
            )

        # Emoji
        if not self.allow_emoji and self._has_emoji(text):
            violations.append("Emoji not allowed")

        # Pilot / deployment claims -- built in, not configurable per voice
        violations.extend(claim_violations(text))

        return GuardrailResult(
            passed=len(violations) == 0,
            violations=violations,
        )

    @classmethod
    def from_voice_yaml(cls, path: str, platform: str = 'x_twitter') -> 'ContentGuardrails':
        """Load guardrails from a voice.yaml file."""
        import yaml
        with open(path) as f:
            voice = yaml.safe_load(f)

        anti_patterns = []
        phrases = voice.get('phrases', {})
        anti_patterns.extend(phrases.get('avoid', []))

        plat = voice.get('platforms', {}).get(platform, {})
        max_length = plat.get('max_length', 280)

        hashtag_str = str(plat.get('hashtags', '2'))
        max_hashtags = int(re.search(r'\d+', hashtag_str.split('-')[-1].split('_')[0]).group()) if re.search(r'\d+', hashtag_str) else 2

        tone = voice.get('voice', {}).get('tone', {})
        allow_emoji = plat.get('emojis', 'none') != 'none'

        return cls(
            anti_patterns=anti_patterns,
            max_length=max_length,
            max_hashtags=max_hashtags,
            allow_emoji=allow_emoji,
            max_exclamations=0 if not tone.get('hype', True) else 1,
        )

    @staticmethod
    def _has_emoji(text: str) -> bool:
        for char in text:
            if unicodedata.category(char) in ('So', 'Sk'):
                return True
        return False
