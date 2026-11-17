"""The creative-director gate: one command scores a draft before the owner sees it.

Order of checks (distribution-engine plan, 17 Nov 2026, section 2):
  a. hard rules, deterministic, no model -- any failure is a reject;
  b. the STEPPS viral checklist (11 checks, from agents/voice-enforcer.md);
  c. a 10-point quality rubric.
b and c come from a model that is NOT the drafter (non-Anthropic, via
OpenRouter). When that model cannot be reached the piece is "unscored" --
never self-graded.

No test here touches the network: the link verifier, the credential broker
and the model call are all replaced.
"""
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "lib"))
sys.path.insert(0, str(MODULE.parents[1] / "lib"))

import creative_director as cd  # noqa: E402
import guardrails  # noqa: E402

FICTIONAL_CLIENT = "Acme Widgets"  # stands in for the private denylist


class FakeVerifier:
    """LinkVerifier stand-in: every URL answers 200 unless told otherwise."""
    answers = {}

    def verify_url(self, url):
        passed, status = self.answers.get(url, (True, 200))
        return passed, {"url": url, "status_code": status,
                        "reason": None if passed else f"Non-200 status code: {status}"}


def good_model_reply(steps=9, rubric=None):
    names = cd.stepps_checks()
    marks = {n: (1 if i < steps else 0) for i, n in enumerate(names)}
    rub = rubric or {"hook": 2, "problem": 2, "checkable_claim": 2,
                     "one_cta": 1, "channel_fit": 1, "human_voice": 1}
    return json.dumps({"stepps": marks, "rubric": rub, "fixes": ["Name the harness in line one."]})


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    FakeVerifier.answers = {}
    monkeypatch.setattr(cd, "LinkVerifier", FakeVerifier)
    monkeypatch.setattr(cd, "_get_key", lambda: "test-key")

    def no_model(*a, **k):
        raise AssertionError("model called without a test asking for it")
    monkeypatch.setattr(cd, "_call_model", no_model)
    # Logic tests run against fixed thresholds; the shipped policy is tested separately.
    monkeypatch.setitem(cd.load_config(), "thresholds",
                        {"stepps_pass": 8, "stepps_reject": 6, "rubric_pass": 8, "max_attempts": 3})
    monkeypatch.setattr(guardrails, "client_name_patterns",
                        lambda: [re.compile(re.escape(FICTIONAL_CLIENT), re.I)])


PLUR_OK = ("Every AI assistant on your team remembers something now, but per tool and per person. "
           "PLUR keeps corrections as plain files your team owns.")
DF_OK = ("If your AI agent can rewrite its own record, you don't have a record. "
         "Our guide shows a receipt someone else can check.")


def draft(tmp_path, name="d.yaml", **over):
    fields = {"id": "P1", "brand": "plur", "channel": "linkedin", "text": PLUR_OK,
              "link": "https://github.com/plur-ai/plur"}
    fields.update(over)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(fields, sort_keys=False))
    return p


def hard(tmp_path, **over):
    failures, _notes = cd.hard_rule_failures(cd.load_draft(draft(tmp_path, **over)))
    return failures


# ---------------------------------------------------------------- hard rules: PLUR

@pytest.mark.parametrize("text", [
    "PLUR lifts recall accuracy by 34% on LongMemEval.",
    "Recall went from 61.5 % to 80 %.",
])
def test_plur_rejects_benchmark_percentages(tmp_path, text):
    assert any("percentage" in f for f in hard(tmp_path, text=text))


def test_plur_rejects_star_counts(tmp_path):
    assert any("star" in f for f in hard(tmp_path, text="PLUR just passed 1,200 GitHub stars."))
    assert any("star" in f for f in hard(tmp_path, text="Thanks for the 2k stars!"))


def test_plur_allows_asking_for_a_star(tmp_path):
    assert hard(tmp_path, text=PLUR_OK + " Star it on GitHub if it helps.") == []


@pytest.mark.parametrize("text", [
    "Your coding assistant doesn't remember yesterday's correction.",
    "Claude Code does not remember what you told it.",
    "Agent harnesses can't remember anything between sessions.",
])
def test_plur_rejects_assistants_dont_remember(tmp_path, text):
    assert any("remember" in f for f in hard(tmp_path, text=text))


def test_plur_allows_saying_assistants_remember_something(tmp_path):
    assert hard(tmp_path) == []


def test_plur_receipts_need_experimental_and_test_network(tmp_path):
    bare = "Each recall can leave a receipt listing which memories were in context."
    assert any("receipt" in f.lower() for f in hard(tmp_path, text=bare))
    half = bare + " It is experimental."
    assert any("receipt" in f.lower() for f in hard(tmp_path, text=half))
    ok = bare + " It's experimental, and the receipts sit on a test network for now."
    assert hard(tmp_path, text=ok) == []


def test_plur_provenance_counts_as_receipts(tmp_path):
    assert any("receipt" in f.lower() for f in hard(tmp_path, text="PLUR adds provenance to every recall."))


@pytest.mark.parametrize("text", [
    "Receipts are experimental, on a test network, and stored on-chain.",
    "Every memory is anchored onchain.",
])
def test_plur_rejects_on_chain_claims(tmp_path, text):
    assert any("chain" in f for f in hard(tmp_path, text=text))


# ---------------------------------------------------------------- hard rules: Datafund

@pytest.mark.parametrize("text", [
    DF_OK + " Every receipt is anchored on mainnet.",
    DF_OK + " Now with Base mainnet anchoring.",
    DF_OK + " Records are anchored to Base mainnet.",
])
def test_datafund_rejects_mainnet_anchoring(tmp_path, text):
    fails = hard(tmp_path, brand="datafund", text=text, link="https://datafund.io/blog/x/")
    assert any("mainnet" in f for f in fails)


def test_datafund_allows_the_true_statement(tmp_path):
    text = DF_OK + " Payments are real USDC on Base mainnet; anchoring is on Base Sepolia."
    assert hard(tmp_path, brand="datafund", text=text, link="https://datafund.io/blog/x/") == []


def test_datafund_rejects_hashtags(tmp_path):
    fails = hard(tmp_path, brand="datafund", text=DF_OK + " #AI #provenance",
                 link="https://datafund.io/blog/x/")
    assert any("hashtag" in f for f in fails)


def test_datafund_url_fragment_is_not_a_hashtag(tmp_path):
    fails = hard(tmp_path, brand="datafund", text=DF_OK + " https://datafund.io/blog/x/#step-2",
                 link="https://datafund.io/blog/x/#step-2")
    assert fails == []


# ---------------------------------------------------------------- hard rules: both brands

@pytest.mark.parametrize("brand", ["plur", "datafund"])
def test_client_names_from_the_denylist_are_rejected(tmp_path, brand):
    link = "https://datafund.io/a/" if brand == "datafund" else "https://github.com/plur-ai/plur"
    fails = hard(tmp_path, brand=brand, link=link, text=f"We built this with {FICTIONAL_CLIENT}.")
    assert any("client" in f for f in fails)
    assert FICTIONAL_CLIENT not in " ".join(fails)  # the reason never repeats the name


def test_client_check_uses_the_guardrails_denylist(tmp_path):
    assert guardrails.client_name_violations(f"hi {FICTIONAL_CLIENT}")
    assert guardrails.client_name_violations("hi there") == []


def test_pilot_claims_from_guardrails_are_rejected(tmp_path):
    fails = hard(tmp_path, text=PLUR_OK + " We have a live pilot with Big Bank.")
    assert any("pilot" in f.lower() for f in fails)


def test_x_length_counts_the_link(tmp_path):
    # X counts an appended link as one space plus 23 characters (t.co), whatever its real length.
    link = "https://plur.ai/blog/agent-memory-correction-preservation-checklist/"
    fails = hard(tmp_path, channel="x", text="x" * 257, link=link)  # 257 + 1 + 23 = 281
    assert any("length" in f for f in fails)
    assert hard(tmp_path, channel="x", text="x" * 256, link=link) == []  # 256 + 1 + 23 = 280


def test_same_text_fits_linkedin(tmp_path):
    assert hard(tmp_path, channel="linkedin", text=PLUR_OK + " " + "word " * 60) == []


def test_exactly_one_link_none(tmp_path):
    fails = hard(tmp_path, link="(the 0.23 article URL once published)")
    assert any("link" in f for f in fails)


def test_exactly_one_link_two(tmp_path):
    fails = hard(tmp_path, text=PLUR_OK + " Docs: https://docs.plur.ai/start")
    assert any("link" in f for f in fails)


def test_link_repeated_bare_in_text_counts_once(tmp_path):
    assert hard(tmp_path, text=PLUR_OK + "\n\nOpen source: github.com/plur-ai/plur") == []


def test_link_that_does_not_answer_200_is_rejected(tmp_path):
    FakeVerifier.answers["https://github.com/plur-ai/plur"] = (False, 404)
    assert any("404" in f for f in hard(tmp_path))


def test_datafund_403_needs_a_browser_check_not_a_failure(tmp_path):
    url = "https://datafund.io/blog/how-to-give-an-ai-agent-a-record-it-cant-rewrite/"
    FakeVerifier.answers[url] = (False, 403)
    failures, notes = cd.hard_rule_failures(cd.load_draft(
        draft(tmp_path, brand="datafund", text=DF_OK, link=url)))
    assert failures == []
    assert any("browser check" in n for n in notes)


def test_a_403_elsewhere_is_a_failure(tmp_path):
    FakeVerifier.answers["https://github.com/plur-ai/plur"] = (False, 403)
    assert any("403" in f for f in hard(tmp_path))


def test_unknown_brand_or_channel_is_rejected(tmp_path):
    assert any("brand" in f for f in hard(tmp_path, brand="fds"))
    assert any("channel" in f for f in hard(tmp_path, channel="tiktok"))


# ---------------------------------------------------------------- STEPPS checklist reuse

def test_stepps_checklist_comes_from_the_voice_enforcer():
    names = cd.stepps_checks()
    assert len(names) == 11
    assert "Purple Cow" in names and "Platform fit" in names


# ---------------------------------------------------------------- verdict thresholds

@pytest.mark.parametrize("failures,steps,rubric,expected", [
    ([], 8, 8, "pass"),
    ([], 11, 10, "pass"),
    ([], 7, 9, "revise"),
    ([], 9, 7, "revise"),
    ([], 6, 6, "revise"),
    ([], 5, 10, "reject"),          # checklist's own rule: under 6 regenerates
    (["hard rule"], 11, 10, "reject"),
    ([], None, None, "unscored"),
    (["hard rule"], None, None, "reject"),
])
def test_verdict_thresholds(failures, steps, rubric, expected):
    assert cd.decide(failures, steps, rubric) == expected


def test_a_third_attempt_that_still_fails_is_dropped():
    assert cd.decide([], 7, 7, attempts=3) == "reject"
    assert cd.decide([], 7, 7, attempts=2) == "revise"
    assert cd.decide([], 9, 9, attempts=3) == "pass"


# ---------------------------------------------------------------- the model (mocked)

def test_model_scores_are_written_back(tmp_path, monkeypatch):
    calls = []

    def fake(prompt, model, key):
        calls.append((model, key))
        return good_model_reply(steps=9)
    monkeypatch.setattr(cd, "_call_model", fake)
    p = draft(tmp_path)
    gate = cd.check_file(p, use_model=True, log_path=tmp_path / "gate-log.jsonl")
    assert len(calls) == 1 and calls[0][1] == "test-key"
    assert gate["verdict"] == "pass" and gate["steps"] == 9 and gate["rubric"] == 9
    assert gate["model"] == calls[0][0]
    saved = yaml.safe_load(p.read_text())
    assert set(saved["gate"]) >= {"verdict", "steps", "rubric", "reasons", "model", "checked_at"}
    assert saved["text"] == PLUR_OK  # the draft itself is untouched


def test_low_rubric_gives_revise_with_the_models_fixes(tmp_path, monkeypatch):
    weak = {"hook": 1, "problem": 1, "checkable_claim": 1, "one_cta": 1, "channel_fit": 1, "human_voice": 1}
    monkeypatch.setattr(cd, "_call_model", lambda *a: good_model_reply(steps=9, rubric=weak))
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "revise" and gate["rubric"] == 6
    assert "Name the harness in line one." in gate["reasons"]


def test_model_scores_are_clamped(tmp_path, monkeypatch):
    silly = {"hook": 9, "problem": 9, "checkable_claim": 9, "one_cta": 9, "channel_fit": 9, "human_voice": 9}
    monkeypatch.setattr(cd, "_call_model", lambda *a: good_model_reply(steps=11, rubric=silly))
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["rubric"] == 10 and gate["steps"] == 11


def test_the_scoring_model_is_not_anthropic():
    assert not cd.load_config()["model"].startswith("anthropic/")


def test_an_anthropic_scoring_model_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_call_model", lambda *a: good_model_reply())
    monkeypatch.setitem(cd.load_config(), "model", "anthropic/claude-sonnet-4-6")
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored"


def test_unscored_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_get_key", lambda: None)
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored" and gate["steps"] is None and gate["rubric"] is None


def test_unscored_when_the_model_call_fails(tmp_path, monkeypatch):
    def boom(*a):
        raise OSError("connection refused")
    monkeypatch.setattr(cd, "_call_model", boom)
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored"
    assert any("unscored" in r for r in gate["reasons"])


def test_unscored_when_the_model_answers_nonsense(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_call_model", lambda *a: "I think it's great!")
    gate = cd.check_file(draft(tmp_path), use_model=True, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored"


def test_no_model_flag_never_calls_the_model(tmp_path):
    gate = cd.check_file(draft(tmp_path), use_model=False, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored" and gate["model"] == "none"


def test_a_hard_reject_does_not_spend_a_model_call(tmp_path):
    gate = cd.check_file(draft(tmp_path, text="Now with 2k stars."), use_model=True,
                         log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "reject"  # the autouse fixture would raise had the model run


def test_attempts_count_up_across_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "_call_model", lambda *a: good_model_reply(steps=7))
    p = draft(tmp_path)
    verdicts = [cd.check_file(p, use_model=True, log_path=tmp_path / "l.jsonl")["verdict"]
                for _ in range(3)]
    assert verdicts == ["revise", "revise", "reject"]


# ---------------------------------------------------------------- files, log, CLI

def test_markdown_front_matter_draft_keeps_its_body(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("---\nid: D1\nbrand: datafund\nchannel: linkedin\n"
                 "link: https://datafund.io/blog/x/\n---\n" + DF_OK + "\n")
    gate = cd.check_file(p, use_model=False, log_path=tmp_path / "l.jsonl")
    assert gate["verdict"] == "unscored"
    raw = p.read_text()
    assert raw.endswith(DF_OK + "\n")
    fm = yaml.safe_load(raw.split("---\n")[1])
    assert fm["gate"]["verdict"] == "unscored" and fm["id"] == "D1"


def test_one_log_line_per_check(tmp_path):
    log = tmp_path / "gate-log.jsonl"
    cd.check_file(draft(tmp_path, name="a.yaml"), use_model=False, log_path=log)
    cd.check_file(draft(tmp_path, name="b.yaml", id="P2", text="1,000 stars"), use_model=False, log_path=log)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["id"] for x in lines] == ["P1", "P2"]
    assert lines[1]["verdict"] == "reject" and lines[0]["file"].endswith("a.yaml")


def test_default_log_sits_next_to_the_content_ledger(tmp_path):
    campaign = tmp_path / "campaign"
    drafts = campaign / "2026-11" / "drafts"
    drafts.mkdir(parents=True)
    (campaign / "content-ledger.csv").write_text("id\n")
    assert cd.default_log_path(drafts / "d.yaml") == campaign / "gate-log.jsonl"


def test_cli_prints_a_table(tmp_path, capsys):
    folder = tmp_path / "batch"
    folder.mkdir()
    draft(folder, name="a.yaml")
    draft(folder, name="b.yaml", id="P2", text="Now with 1,000 GitHub stars.")
    (folder / "content-ledger.csv").write_text("id\n")
    assert cd.main(["check", str(folder), "--no-model"]) == 0
    out = capsys.readouterr().out
    assert "STEPPS" in out and "rubric" in out
    assert re.search(r"P1\s+unscored", out) and re.search(r"P2\s+reject\s+-\s+-\s+.*star", out)
    assert (folder / "gate-log.jsonl").exists()


def test_a_file_missing_fields_is_reported_not_crashed(tmp_path, capsys):
    p = tmp_path / "bad.yaml"
    p.write_text("id: X\nbrand: plur\n")
    (tmp_path / "content-ledger.csv").write_text("id\n")
    assert cd.main(["check", str(p), "--no-model"]) == 0
    assert "missing" in capsys.readouterr().out


# ---------------------------------------------------------------- fixes after the first real batch (2026-11-17)

LONG_LINK = ("https://plur.ai/blog/agent-memory-tool-provenance-checklist/"
             "?utm_source=x&utm_medium=social&utm_campaign=distribution_2026_11&utm_content=trace-fact")


def test_x_counts_every_link_as_23_characters(tmp_path):
    body = "x" * 240
    # 240 chars + space + link counted as 23 = 264, inside X's 280 even though the raw URL is long
    assert not any("length" in f for f in hard(tmp_path, channel="x", text=body + " " + LONG_LINK, link=LONG_LINK))
    assert not any("length" in f for f in hard(tmp_path, channel="x", text=body, link=LONG_LINK))
    assert any("length" in f for f in hard(tmp_path, channel="x", text="x" * 270 + " " + LONG_LINK, link=LONG_LINK))


def test_receipts_rule_reads_the_prose_not_the_url(tmp_path):
    text = "When your agent recalls a fact, can you trace where it came from? " + LONG_LINK
    assert not any("receipts" in f for f in hard(tmp_path, text=text, link=LONG_LINK))
    assert any("receipts" in f for f in hard(tmp_path, text="PLUR leaves a receipt for every recall. " + LONG_LINK,
                                              link=LONG_LINK))


def test_brand_both_applies_both_rule_sets(tmp_path):
    assert hard(tmp_path, brand="both", text=PLUR_OK) == []
    assert any("hashtag" in f.lower() for f in hard(tmp_path, brand="both", text=PLUR_OK + " #AI"))
    assert any("percentage" in f for f in hard(tmp_path, brand="both", text=PLUR_OK + " Up 34% this week."))


def test_index_files_are_not_drafts(tmp_path):
    draft(tmp_path, name="a.yaml")
    (tmp_path / "INDEX.md").write_text("# Batch\n\n| id | date |\n|---|---|\n")
    found = [p.name for p in cd.collect_paths(tmp_path)] if hasattr(cd, "collect_paths") else None
    assert found is not None, "collect_paths(dir) should list the draft files in a batch folder"
    assert "INDEX.md" not in found and "a.yaml" in found


def test_shipped_policy_makes_stepps_advisory():
    """Owner decision 2026-11-18: pass on the rubric plus hard rules; STEPPS is only recorded."""
    import yaml as _yaml
    t = _yaml.safe_load(cd.CONFIG_PATH.read_text())["thresholds"]
    assert t["stepps_pass"] == 0 and t["stepps_reject"] == 0
    assert t["rubric_pass"] == 8

