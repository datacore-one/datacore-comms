#!/usr/bin/env python3
"""Creative-director gate: score a draft before the owner sees it.

    python3 .datacore/modules/comms/lib/creative_director.py check <file-or-dir> [--no-model] [--log PATH]

A draft is one YAML file, or markdown with front matter, carrying
``id, brand (plur|datafund), channel (x|linkedin|reddit|devto|blog|hn), text, link``
(markdown may leave ``text`` out and use its body).

Checks, in order:
  a. Hard rules -- deterministic, no model. Any failure is a reject.
     The pilot/deployment claim refusals and the client-name denylist come from
     ``guardrails``; the link check from the core ``link_verifier``.
  b. STEPPS viral checklist, 0-11. The 11 checks are read from
     ``agents/voice-enforcer.md`` (the checklist the module already uses).
  c. A 10-point quality rubric.
  b and c are scored by a model from a different family than the drafter
  (non-Anthropic, through OpenRouter; key only via ``creds``). If that model
  cannot be reached the piece is "unscored" -- never self-graded.

Verdict: pass = no hard-rule failure, STEPPS >= 8 and rubric >= 8. Otherwise
revise (with the fixes) or reject. The result is written back into the draft as
``gate: {verdict, steps, rubric, reasons, model, checked_at, attempts}`` and one
JSON line per check is appended to ``gate-log.jsonl`` beside the campaign's
``content-ledger.csv``.

Network: the link check (GET/HEAD of the draft's one link) and the scoring
call. Nothing is posted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import yaml

MODULE_DIR = Path(__file__).resolve().parent.parent
CORE_LIB = Path(os.environ.get("DATACORE_LIB") or MODULE_DIR.parent.parent / "lib")
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
for _p in (str(Path(__file__).resolve().parent), str(CORE_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import guardrails  # noqa: E402
from link_verifier import LinkVerifier  # noqa: E402

CONFIG_PATH = MODULE_DIR / "config" / "creative-director.yaml"
VOICE_ENFORCER = MODULE_DIR / "agents" / "voice-enforcer.md"
REQUIRED = ("id", "brand", "channel", "text", "link")

# Rubric: criterion -> (max points, what earns them). Sums to 10.
RUBRIC = {
    "hook": (2, "the first line makes the reader stop: a hook, not a preamble"),
    "problem": (2, "names a concrete problem the reader recognises"),
    "checkable_claim": (2, "makes one specific claim the reader can check"),
    "one_cta": (1, "exactly one call to action"),
    "channel_fit": (1, "fits the channel's format, length and norms"),
    "human_voice": (2, "sounds like a person, not marketing copy"),
}

_CONFIG: Optional[dict] = None


def load_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return _CONFIG


# ------------------------------------------------------------------ drafts

class Draft:
    def __init__(self, path: Path, meta: dict, body: Optional[str]):
        self.path, self.meta, self.body = path, meta, body
        text = meta.get("text")
        if text is None and body is not None and body.strip():
            text = body.strip()
        self.text = str(text) if text is not None else None
        self.id = str(meta.get("id", path.stem))
        self.brand = str(meta.get("brand", "")).lower()
        self.channel = str(meta.get("channel", "")).lower()
        self.link = str(meta.get("link", "") or "").strip()

    def missing(self) -> List[str]:
        have = {"id": self.meta.get("id"), "brand": self.brand, "channel": self.channel,
                "text": self.text, "link": self.link}
        return [k for k in REQUIRED if not have[k]]


def _split_front_matter(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    if not raw.startswith("---\n"):
        return None, None
    end = raw.find("\n---\n", 4)
    if end < 0:
        return None, None
    return yaml.safe_load(raw[4:end]) or {}, raw[end + 5:]


def load_draft(path: Path) -> Draft:
    path = Path(path)
    raw = path.read_text()
    if path.suffix == ".md":
        meta, body = _split_front_matter(raw)
        return Draft(path, meta or {}, body if meta is not None else raw)
    meta = yaml.safe_load(raw) or {}
    return Draft(path, meta if isinstance(meta, dict) else {}, None)


class _BlockDumper(yaml.SafeDumper):
    pass


_BlockDumper.add_representer(
    str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None))


def _dump(data: dict) -> str:
    return yaml.dump(data, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=1000)


def write_gate(draft: Draft, gate: dict) -> None:
    meta = dict(draft.meta)
    meta["gate"] = gate
    if draft.path.suffix == ".md":
        body = draft.body if draft.body is not None else ""
        draft.path.write_text("---\n" + _dump(meta) + "---\n" + body)
    else:
        draft.path.write_text(_dump(meta))


# ------------------------------------------------------------------ links

_URL = re.compile(r"https?://[^\s)\]}>\"']+")
_BARE = re.compile(r"(?<![@/\w.])(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s)\]}>\"']*", re.I)


def _norm(url: str) -> str:
    url = re.sub(r"[.,;:!?]+$", "", url.strip())
    if not re.match(r"https?://", url):
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/")
    return host + path + ("?" + parts.query if parts.query else "")


def _text_links(text: str) -> Dict[str, str]:
    found = {}
    for rx in (_URL, _BARE):
        for m in rx.finditer(text):
            raw = re.sub(r"[.,;:!?]+$", "", m.group(0))
            found.setdefault(_norm(raw), raw if raw.startswith("http") else "https://" + raw)
    return found


def check_link(url: str) -> Tuple[str, str]:
    """('ok' | 'browser' | 'fail', detail) for one URL."""
    passed, details = LinkVerifier().verify_url(url)
    code = details.get("status_code")
    host = (urlsplit(url).hostname or "").lower()
    browser_hosts = load_config().get("browser_check_hosts", [])
    on_browser_host = any(host == h or host.endswith("." + h) for h in browser_hosts)
    if passed and code == 200:
        return "ok", "200"
    if passed:  # e.g. a bot challenge: the page exists but a script cannot see it
        return "browser", f"{code}: {details.get('error') or 'not a plain 200'}"
    if code == 403 and on_browser_host:
        return "browser", "403 from a host that blocks scripts"
    return "fail", str(details.get("reason") or details.get("error") or code)


# ------------------------------------------------------------------ hard rules

_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|per\s?cent\b)", re.I)
_STARS = re.compile(r"(?:\b\d[\d,.]*\s*[kK]?\+?\s+(?:GitHub\s+)?stars?\b|★\s*\d)", re.I)
_NEG = r"(?:don[’']?t|do not|doesn[’']?t|does not|can[’']?t|cannot|can not|never|won[’']?t|will not)"
_DONT_REMEMBER = re.compile(
    r"\b(?:assistants?|agents?|harness(?:es)?|AI|LLMs?|models?|chatbots?|copilots?|Claude(?: Code)?"
    r"|Cursor|Codex|tools?|it|they)\b[^.!?\n]{0,30}?\b" + _NEG + r"\s+remember", re.I)
_RECEIPTS = re.compile(r"\b(?:receipts?|provenance)\b", re.I)
_ON_CHAIN = re.compile(r"\bon[- ]?chain\b|\bon the blockchain\b|\bmainnet\b", re.I)
_MAINNET_ANCHOR = re.compile(
    r"\banchor\w*\s+(?:on|to|in)\s+(?:the\s+)?(?:Base\s+)?mainnet\b|\b(?:Base\s+)?mainnet\s+anchor\w*", re.I)
_HASHTAG = re.compile(r"(?<![\w&#])#[A-Za-z_]\w*")


def _plur_rules(text: str) -> List[str]:
    text = _BARE.sub(" ", _URL.sub(" ", text))  # judge the prose, not words inside link URLs
    out = []
    if _PERCENT.search(text):
        out.append(f"PLUR: no benchmark percentages ('{_PERCENT.search(text).group(0)}')")
    if _STARS.search(text):
        out.append(f"PLUR: no star counts ('{_STARS.search(text).group(0)}')")
    m = _DONT_REMEMBER.search(text)
    if m:
        out.append(f"PLUR: never claim assistants or harnesses don't remember ('{m.group(0)}')")
    if _RECEIPTS.search(text):
        low = text.lower()
        if "experimental" not in low or not ("test network" in low or "testnet" in low):
            out.append("PLUR: receipts/provenance only as 'experimental' and on a 'test network'")
    m = _ON_CHAIN.search(text)
    if m:
        out.append(f"PLUR: no on-chain claim ('{m.group(0)}')")
    return out


def _datafund_rules(text: str) -> List[str]:
    out = []
    m = _MAINNET_ANCHOR.search(text)
    if m:
        out.append(f"Datafund: anchoring is not on mainnet ('{m.group(0)}')")
    stripped = _BARE.sub(" ", _URL.sub(" ", text))
    tags = _HASHTAG.findall(stripped)
    if tags:
        out.append(f"Datafund: no hashtags ({' '.join(tags[:3])})")
    return out


def _both_rules(text: str) -> List[str]:
    return _plur_rules(text) + _datafund_rules(text)


BRAND_RULES = {"plur": _plur_rules, "datafund": _datafund_rules, "both": _both_rules}


def hard_rule_failures(draft: Draft) -> Tuple[List[str], List[str]]:
    """(failures, notes). Any failure rejects; notes are carried as reasons."""
    cfg = load_config()
    failures: List[str] = []
    notes: List[str] = []
    missing = draft.missing()
    if missing:
        return [f"missing field(s): {', '.join(missing)}"], notes
    text = draft.text

    if draft.brand not in cfg.get("brands", []):
        failures.append(f"unknown brand '{draft.brand}' (expected {'|'.join(cfg.get('brands', []))})")
    else:
        failures.extend(BRAND_RULES[draft.brand](text))

    failures.extend(guardrails.client_name_violations(text))
    if guardrails.client_name_patterns() is None:
        notes.append("client-name check not run: no private denylist on this host")
    failures.extend(guardrails.claim_violations(text))

    # Links: the link field plus every link in the text, counted once each.
    links = _text_links(text)
    if re.match(r"https?://", draft.link):
        links.setdefault(_norm(draft.link), draft.link)
    in_text = re.match(r"https?://", draft.link) and _norm(draft.link) in _text_links(text)

    limits = cfg.get("channel_limits", {})
    if draft.channel not in limits:
        failures.append(f"unknown channel '{draft.channel}' (expected {'|'.join(limits)})")
    else:
        if draft.channel == "x":
            # X shortens every link to 23 characters (t.co), whatever its real length
            body = _URL.sub("x" * 23, text)
            length = len(body) + (0 if in_text or not re.match(r"https?://", draft.link) else 1 + 23)
        else:
            length = len(text) + (0 if in_text or not re.match(r"https?://", draft.link)
                                  else 1 + len(draft.link))
        if length > limits[draft.channel]:
            failures.append(f"length {length} over the {draft.channel} limit of {limits[draft.channel]} "
                            "(link included)")

    if len(links) != 1:
        failures.append(f"needs exactly one link, found {len(links)}")
    else:
        url = next(iter(links.values()))
        status, detail = check_link(url)
        if status == "fail":
            failures.append(f"link does not answer 200: {url} ({detail})")
        elif status == "browser":
            notes.append(f"link needs a browser check: {url} ({detail})")
    return failures, notes


# ------------------------------------------------------------------ model scoring

def stepps_checks() -> List[str]:
    """The 11 viral-checklist names, read from the voice-enforcer agent's table."""
    return [name for name, _desc in _stepps_rows()]


def _stepps_rows() -> List[Tuple[str, str]]:
    src = VOICE_ENFORCER.read_text()
    section = src.split("## Viral Checklist", 1)[1].split("**Total", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[1] and cells[1] != "Check" and not set(cells[1]) <= set("-"):
            rows.append((cells[1], cells[2]))
    return rows


def build_prompt(draft: Draft) -> str:
    checklist = "\n".join(f"- {n}: {d}" for n, d in _stepps_rows())
    rubric = "\n".join(f"- {k} (0-{pts}): {desc}" for k, (pts, desc) in RUBRIC.items())
    keys = ", ".join(f'"{n}": 0 or 1' for n in stepps_checks())
    rkeys = ", ".join(f'"{k}": 0-{pts}' for k, (pts, _d) in RUBRIC.items())
    return f"""You are an independent creative director. Another model wrote this draft; judge it strictly.

Brand: {draft.brand}
Channel: {draft.channel}
Link: {draft.link}
Draft:
<<<
{draft.text}
>>>

1. Viral checklist: mark each item 1 if clearly present, else 0.
{checklist}

2. Quality rubric: score each criterion within its range.
{rubric}

3. Fixes: up to three specific edits that would raise the scores most. Quote the words to change.

Answer with JSON only, no prose:
{{"stepps": {{{keys}}}, "rubric": {{{rkeys}}}, "fixes": ["..."]}}"""


def _get_key() -> Optional[str]:
    """OpenRouter key through the credential broker only. Never printed."""
    creds = shutil.which("creds") or str(DATA_DIR / ".datacore" / "bin" / "creds")
    try:
        r = subprocess.run([creds, "get", "OPENROUTER_API_KEY", "--consumer", "creative-director"],
                           capture_output=True, text=True, timeout=90)
    except Exception as e:  # noqa: BLE001
        print(f"[creative-director] creds unavailable: {type(e).__name__}", file=sys.stderr)
        return None
    if r.returncode != 0 or not r.stdout.strip():
        print(f"[creative-director] creds declined: {(r.stderr or '').strip()[:200]}", file=sys.stderr)
        return None
    return r.stdout.strip()


def _call_model(prompt: str, model: str, key: str) -> str:
    """One chat completion on OpenRouter. Inference only; publishes nothing."""
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({"model": model, "temperature": 0.2, "max_tokens": 1500,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def _key_of(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def parse_scores(reply: str) -> Optional[Tuple[int, int, List[str]]]:
    """(steps, rubric, fixes) from the model's JSON, or None if it is unusable."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply[start:end + 1])
        marks = {_key_of(k): v for k, v in dict(data["stepps"]).items()}
        rub = dict(data["rubric"])
        names = stepps_checks()
        if any(_key_of(n) not in marks for n in names) or any(k not in rub for k in RUBRIC):
            return None
        steps = sum(1 for n in names if float(marks[_key_of(n)]) >= 1)
        rubric = sum(int(max(0, min(pts, round(float(rub[k]))))) for k, (pts, _d) in RUBRIC.items())
        fixes = [str(f) for f in (data.get("fixes") or []) if str(f).strip()][:3]
    except (ValueError, TypeError, KeyError):
        return None
    return steps, rubric, fixes


def score(draft: Draft) -> Tuple[Optional[Tuple[int, int, List[str]]], str, str]:
    """(scores or None, model used or 'none', why unscored)."""
    model = str(load_config().get("model", ""))
    if not model or model.lower().startswith("anthropic/"):
        return None, "none", f"unscored: scoring model '{model}' is the drafter's family; refused"
    key = _get_key()
    if not key:
        return None, "none", "unscored: OpenRouter key not available through creds"
    try:
        reply = _call_model(build_prompt(draft), model, key)
    except Exception as e:  # noqa: BLE001
        return None, "none", f"unscored: scoring model unavailable ({type(e).__name__}: {str(e)[:80]})"
    parsed = parse_scores(reply or "")
    if parsed is None:
        return None, "none", f"unscored: {model} gave no usable scores"
    return parsed, model, ""


# ------------------------------------------------------------------ verdict

def decide(failures: List[str], steps: Optional[int], rubric: Optional[int], attempts: int = 1) -> str:
    t = load_config().get("thresholds", {})
    if failures:
        return "reject"
    if steps is None or rubric is None:
        return "unscored"
    if steps >= t.get("stepps_pass", 8) and rubric >= t.get("rubric_pass", 8):
        return "pass"
    if steps < t.get("stepps_reject", 6) or attempts >= t.get("max_attempts", 3):
        return "reject"
    return "revise"


def default_log_path(draft_path: Path) -> Path:
    """gate-log.jsonl beside the nearest content-ledger.csv above the draft."""
    for d in list(Path(draft_path).resolve().parents)[:6]:
        if (d / "content-ledger.csv").is_file():
            return d / "gate-log.jsonl"
    print(f"[creative-director] no content-ledger.csv above {draft_path}; logging beside the draft",
          file=sys.stderr)
    return Path(draft_path).resolve().parent / "gate-log.jsonl"


def check_file(path: Path, use_model: bool = True, log_path: Optional[Path] = None) -> dict:
    draft = load_draft(Path(path))
    prior = draft.meta.get("gate") if isinstance(draft.meta.get("gate"), dict) else {}
    attempts = int(prior.get("attempts") or 0) + 1
    failures, notes = hard_rule_failures(draft)
    steps = rubric = None
    fixes: List[str] = []
    model, why = "none", ""
    if not failures:
        if use_model:
            scores, model, why = score(draft)
            if scores:
                steps, rubric, fixes = scores
        else:
            why = "unscored: run with --no-model"
    verdict = decide(failures, steps, rubric, attempts)
    t = load_config().get("thresholds", {})

    reasons = list(failures)
    if why:
        reasons.append(why)
    if steps is not None and verdict != "pass":
        if steps < t.get("stepps_pass", 8):
            reasons.append(f"STEPPS {steps}/11, needs {t.get('stepps_pass', 8)}")
        if rubric < t.get("rubric_pass", 8):
            reasons.append(f"rubric {rubric}/10, needs {t.get('rubric_pass', 8)}")
        if verdict == "reject" and steps >= t.get("stepps_reject", 6):
            reasons.append(f"dropped: attempt {attempts} still below the bar")
        reasons.extend(fixes)
    reasons.extend(notes)

    gate = {"verdict": verdict, "steps": steps, "rubric": rubric, "reasons": reasons,
            "model": model, "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempts": attempts}
    write_gate(draft, gate)
    log = Path(log_path) if log_path else default_log_path(draft.path)
    with log.open("a") as fh:
        fh.write(json.dumps({"file": str(draft.path), "id": draft.id, "brand": draft.brand,
                             "channel": draft.channel, **gate}) + "\n")
    return {"id": draft.id, **gate}


# ------------------------------------------------------------------ CLI

_NOT_DRAFTS = {"index.md", "readme.md"}


def collect_paths(target: Path) -> List[Path]:
    """Draft files in a batch folder: YAML or markdown, skipping INDEX.md / README.md."""
    if target.is_file():
        return [target]
    return sorted(p for p in target.iterdir()
                  if p.is_file() and p.suffix in (".yaml", ".yml", ".md") and not p.name.startswith(".")
                  and p.name.lower() not in _NOT_DRAFTS)


_drafts_in = collect_paths


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="creative_director", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="score draft file(s) and write the gate back")
    c.add_argument("target")
    c.add_argument("--no-model", action="store_true", help="hard rules only; pieces stay unscored")
    c.add_argument("--log", help="gate log path (default: beside content-ledger.csv)")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 2
    rows = [check_file(p, use_model=not args.no_model, log_path=args.log) for p in _drafts_in(target)]

    fmt = "{:<16} {:<9} {:>6} {:>6}  {}"
    print(fmt.format("id", "verdict", "STEPPS", "rubric", "first reason"))
    for r in rows:
        dash = lambda v: "-" if v is None else str(v)  # noqa: E731
        print(fmt.format(r["id"][:16], r["verdict"], dash(r["steps"]), dash(r["rubric"]),
                         (r["reasons"][0] if r["reasons"] else "")[:110]))
    models = sorted({r["model"] for r in rows if r["model"] != "none"})
    print(f"\nscored by: {', '.join(models) or 'no model'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
