#!/usr/bin/env python3
"""Evaluate engagement drafts through persona evaluators before Telegram.

Runs a focused panel of evaluators on short-form content (tweets/replies).
Uses Claude Sonnet via CLI for each evaluator.

Pipeline: draft → principles gate (5th evaluator, hard gate) →
          → 4 evaluators → if approved → Telegram/auto-post
                         → if rejected → auto-discard + log

Principles gate (5th evaluator):
  Score < 0.70 = hard reject, never overridden by consensus.
  Applied BEFORE consensus is computed.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))

# Focused evaluator panel for engagement content
# These are the most relevant personas for short-form social content
ENGAGEMENT_EVALUATORS = ['voice', 'hemingway', 'orwell', 'naturalness']

EVALUATOR_PROMPTS = {
    'voice': """You are evaluating a REPLY tweet for @FairDataSociety.

CRITICAL: This is a REPLY. The rules are: (1) don't be smug, (2) add to the conversation, (3) make OP feel heard.

AUTOMATIC FAIL (score 0.2 or below) if the reply:
- Uses "The step most miss:", "Most people don't realize", "What few understand" — condescending
- Uses "X isn't a feature — it's the layer that..." or similar authority-claiming frames
- Starts with "The real X isn't Y" or "The real fix is" — implies knowing better than OP
- Opens with "Actually..." or "The problem isn't..." — lectures OP
- Uses the same opener pattern as the examples every time (formulaic = low score)

Score HIGH if the reply:
- Sounds like something a thoughtful human would genuinely say
- Adds one specific thing to OP's point without upstaging them
- Varies the structure (question, short agreement, experience, extension — any works)
- Is under 140 characters

Examples of GOOD reply voice:
- "Policy changes. Architecture doesn't." (tight, no preamble)
- "What's the hardest part in practice — getting regulators to accept ZK proof?" (genuine question)
- "We hit the same wall building Fairdrop. Encrypt-before-upload changed what we could promise." (experience)
- "And if the company gets acquired, that policy goes with it." (concrete extension)

Examples of BAD reply voice (score LOW):
- "The real vulnerability isn't X — it's Y" (negates OP)
- "The step most miss: [insight]" (condescending)
- "This is what happens when principles live in a press release instead of the architecture." (great once, never again)

Evaluate: Is this additive without being smug? Does it vary structure? Under 140 chars?""",

    'hemingway': """You are Ernest Hemingway evaluating a tweet reply.
Your criteria: brevity, directness, punch. Every word must earn its place.
The best tweets are 1-2 sentences. Under 140 chars is ideal. Over 200 chars should score low.
Cut the fluff. Is this tight? Does it hit hard? Would you cut any words?
If the reply has unnecessary explanation after the point lands, score it LOW.""",

    'orwell': """You are George Orwell evaluating a tweet reply.
Your criteria: clarity of thought, no pretentiousness, no jargon for jargon's sake.
Is the thinking clear? Could a smart non-expert follow? Is it honest, not performative?

IMPORTANT CONTEXT: This is a tweet reply (max 280 chars), not an essay.
- Brevity is a VIRTUE on Twitter. A 40-char reply that makes one clear point is GOOD.
- Aphoristic/punchy replies are the native format. Don't penalize conciseness as "cryptic."
- Score the CLARITY of the point being made, not the depth of explanation.
- A reply that makes a reader think "huh, good point" in 5 words is Orwellian.

Score HIGH if: the point is clear, honest, and free of jargon or pretension.
Score LOW if: the reply uses buzzwords to sound smart, is genuinely unclear, or is performative.""",

    'naturalness': """You are checking whether a tweet reply sounds human or AI-generated.

AI-generated replies have tell-tale patterns:
- Formulaic structure (VALIDATE → BUILD → insight, every single time)
- Authority-claiming phrases: "The step most miss:", "What few realize", "The real X is Y"
- Consistent register: always "builder energy", always forward-looking, never casual
- Too-clean insight delivery: every reply lands a quotable epigram
- Starting with the same validation words: "Spot on.", "Exactly.", "Nailed it." every time

Human replies vary: sometimes a question, sometimes just "this", sometimes a brief observation, sometimes a personal experience. They're not all insightful.

Score HIGH (0.8+) if: sounds like a real person, varied structure, specific not generic, would fit naturally in the thread
Score LOW (0.3 or below) if: sounds like it was generated, uses AI content patterns, too polished/formulaic, reads like a brand account

Be harsh. If it sounds like a bot wrote it, score it low.""",
}

EVALUATION_TEMPLATE = """# Tweet Reply Evaluation

## Evaluator
{evaluator_prompt}

## Target tweet being replied to
Author: {target_author}
Content: {target_content}

## Draft reply to evaluate
{draft_reply}

## Instructions
Score this draft reply on a scale of 0.0 to 1.0 and provide brief feedback.

You MUST respond with ONLY this YAML block:
```yaml
evaluator: {evaluator_name}
score: <number between 0.0 and 1.0>
feedback: "<one sentence feedback>"
```

Nothing else. Just the YAML block."""


@dataclass
class EvalResult:
    evaluator: str
    score: float
    feedback: str


@dataclass
class DraftEvaluation:
    scores: Dict[str, float]
    feedback: Dict[str, str]
    consensus: float
    variance: float
    decision: str  # approved, needs_revision, rejected
    results: List[EvalResult]
    principles_score: float = 0.7  # Hard gate score (< 0.70 = auto-reject)
    principles_feedback: str = ""

    @property
    def summary_line(self) -> str:
        parts = [f"{e}: {s:.0%}" for e, s in self.scores.items()]
        principles_tag = "" if self.principles_score >= 0.70 else f" | PRINCIPLES: {self.principles_score:.0%} FAIL"
        return f"[{self.decision}] {self.consensus:.0%} — " + " | ".join(parts) + principles_tag

    @property
    def feedback_block(self) -> str:
        lines = []
        for r in self.results:
            emoji = "+" if r.score >= 0.7 else "-"
            lines.append(f"  {emoji} {r.evaluator}: {r.feedback}")
        return "\n".join(lines)


def evaluate_draft(
    draft_reply: str,
    target_author: str,
    target_content: str,
    evaluators: List[str] = None,
    model: str = "sonnet",
    skip_principles: bool = False,
) -> DraftEvaluation:
    """Run persona evaluators on an engagement draft.

    Phase 0: Principles gate (hard gate, score < 0.70 = auto-reject)
    Phase 1: 4 persona evaluators for quality/consensus

    Args:
        draft_reply: The draft tweet text
        target_author: Who we're replying to
        target_content: What they said
        evaluators: List of evaluator names (default: ENGAGEMENT_EVALUATORS)
        model: Claude model to use (default: sonnet)
        skip_principles: Skip principles gate (for testing/debugging)

    Returns:
        DraftEvaluation with scores, consensus, decision, and principles fields
    """
    evaluators = evaluators or ENGAGEMENT_EVALUATORS

    # Phase 0: Principles gate (hard gate — applied before quality evaluators)
    principles_score = 0.7
    principles_feedback = ""
    if not skip_principles:
        try:
            from principles_evaluator import evaluate_principles, PRINCIPLES_THRESHOLD
            pr = evaluate_principles(draft_reply, target_content, target_author)
            principles_score = pr.score
            principles_feedback = pr.feedback
            if not pr.passed:
                # Hard reject — don't run quality evaluators
                return DraftEvaluation(
                    scores={"principles": principles_score},
                    feedback={"principles": principles_feedback},
                    consensus=0.0,
                    variance=0.0,
                    decision="rejected",
                    results=[EvalResult(evaluator="principles", score=principles_score, feedback=principles_feedback)],
                    principles_score=principles_score,
                    principles_feedback=principles_feedback,
                )
        except ImportError:
            pass  # principles_evaluator not available — skip gate

    results = []

    for evaluator in evaluators:
        evaluator_prompt = EVALUATOR_PROMPTS.get(evaluator, f"You are the {evaluator} evaluator.")

        prompt = EVALUATION_TEMPLATE.format(
            evaluator_prompt=evaluator_prompt,
            target_author=target_author,
            target_content=target_content[:300],
            draft_reply=draft_reply,
            evaluator_name=evaluator,
        )

        result = _run_evaluator(evaluator, prompt, model)
        print(f"    [{evaluator}] score={result.score:.2f} feedback={result.feedback[:80]}")
        results.append(result)

    # Compute consensus
    scores = {r.evaluator: r.score for r in results}
    feedback = {r.evaluator: r.feedback for r in results}

    values = list(scores.values())
    n = len(values)
    mean = sum(values) / n if n else 0.5
    variance = sum((x - mean) ** 2 for x in values) / n if n else 0.0

    # Decision thresholds — tuned for engagement content (informal, short)
    # Previous: 0.75/0.60 were too strict, causing 100% rejection
    if variance > 0.1:
        decision = 'approved' if mean >= 0.70 else 'needs_revision'
    else:
        if mean >= 0.65:
            decision = 'approved'
        elif mean >= 0.50:
            decision = 'needs_revision'
        else:
            decision = 'rejected'

    print(f"    Consensus: {mean:.2f} variance={variance:.3f} → {decision}")

    return DraftEvaluation(
        scores=scores,
        feedback=feedback,
        consensus=round(mean, 3),
        variance=round(variance, 4),
        decision=decision,
        results=results,
        principles_score=principles_score,
        principles_feedback=principles_feedback,
    )


def _run_evaluator(evaluator: str, prompt: str, model: str) -> EvalResult:
    """Run a single evaluator via OpenRouter API (or Claude CLI fallback)."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if api_key:
        return _run_evaluator_openrouter(evaluator, prompt, api_key)

    # Fallback to Claude CLI — strip ANTHROPIC_API_KEY to use Max subscription OAuth
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") and k != "ANTHROPIC_API_KEY"}
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = os.environ.get("HOME", str(Path.home()))

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "text",
             "--no-session-persistence", "--max-turns", "1"],
            input=prompt, capture_output=True, text=True,
            cwd=str(DATA_DIR), env=env, timeout=45,
        )
        if result.returncode != 0:
            return EvalResult(evaluator=evaluator, score=0.5,
                              feedback=f"Error: {(result.stderr or result.stdout)[:100]}")
        return _parse_result(evaluator, result.stdout)
    except subprocess.TimeoutExpired:
        return EvalResult(evaluator=evaluator, score=0.5, feedback="Timed out")
    except Exception as e:
        return EvalResult(evaluator=evaluator, score=0.5, feedback=f"Error: {str(e)[:80]}")


def _run_evaluator_openrouter(evaluator: str, prompt: str, api_key: str) -> EvalResult:
    """Run evaluator via OpenRouter API — lightweight HTTP call."""
    import urllib.request
    import urllib.error

    body = json.dumps({
        "model": "anthropic/claude-sonnet-4",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.3,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://fairdatasociety.org",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        return _parse_result(evaluator, text)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:100] if hasattr(e, 'read') else str(e)
        return EvalResult(evaluator=evaluator, score=0.5, feedback=f"API error {e.code}: {err_body}")
    except Exception as e:
        return EvalResult(evaluator=evaluator, score=0.5, feedback=f"Error: {str(e)[:80]}")


def _parse_result(evaluator: str, output: str) -> EvalResult:
    """Parse YAML score and feedback from evaluator output."""
    # Try YAML block first
    yaml_match = re.search(r'```yaml\s*(.*?)\s*```', output, re.DOTALL)
    text = yaml_match.group(1) if yaml_match else output

    score_match = re.search(r'score:\s*([\d.]+)', text)
    score = float(score_match.group(1)) if score_match else 0.5
    score = max(0.0, min(1.0, score))

    feedback_match = re.search(r'feedback:\s*["\']?(.+?)["\']?\s*(?:\n|$)', text, re.DOTALL)
    feedback = feedback_match.group(1).strip() if feedback_match else "No feedback"

    return EvalResult(evaluator=evaluator, score=score, feedback=feedback)


if __name__ == "__main__":
    # Quick test
    result = evaluate_draft(
        draft_reply="Good stack. Missing one layer: sovereign storage. Where does the data live after compute?",
        target_author="@maxdesalle",
        target_content="The sovereign stack: @zcash for storing value, @nym for connectivity, @arcium for compute",
    )
    print(result.summary_line)
    print(result.feedback_block)
