"""MEM-18: Nothing public ever claims a live pilot or deployment with an institution unless one
has actually happened.

Kind: deterministic -- the real publishing gates of the comms module, offline:
  * ContentGuardrails.from_voice_yaml over every REAL voice.yaml in the installation (the check
    the autonomous poster runs before posting without a human);
  * XPoster.post (the one function every X post goes through) with the network call replaced
    by a tripwire that raises -- so nothing is sent and nothing is attested.
No record of executed pilots exists in the system, so no claim of one can be verified: the
gates must stop every such claim for a human.

Seeded failure: drafts claiming "our live pilot with the Port of Rotterdam", "now deployed at
Ljubljana University Medical Centre", "in production with the City of Vienna".
Red today: no voice file lists pilot/deployment claims, and XPoster.post has no claim check --
the drafts reach the network call.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from guardrails import ContentGuardrails  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
VOICES = sorted(p for p in ROOT.glob("*/.datacore/voice.yaml")) + \
    sorted(ROOT.glob("*/1-tracks/comms/positioning/voice.yaml"))

CLAIMS = [
    "Our live pilot with the Port of Rotterdam shows fair data works at scale.",
    "Verity is now deployed at Ljubljana University Medical Centre.",
    "Fairdrop is in production with the City of Vienna since May.",
]
NEUTRAL = "We are talking to hospitals about fair data sharing. Early days."


def test_voice_files_found():
    assert VOICES, "no voice.yaml found -- the guardrail check below would be vacuous"


@pytest.mark.parametrize("voice", VOICES, ids=lambda p: str(p.relative_to(ROOT)).split("/")[0])
@pytest.mark.parametrize("claim", CLAIMS)
def test_guardrails_stop_an_unverified_pilot_claim(voice, claim):
    g = ContentGuardrails.from_voice_yaml(str(voice))
    g.max_length = 10_000          # length is not what is under test
    assert not g.check(claim).passed, f"{voice.parent.parent.name}: pilot claim passes the guardrails"


@pytest.mark.parametrize("voice", VOICES, ids=lambda p: str(p.relative_to(ROOT)).split("/")[0])
def test_guardrails_let_a_neutral_post_through(voice):
    g = ContentGuardrails.from_voice_yaml(str(voice))
    g.max_length = 10_000
    assert g.check(NEUTRAL).passed, g.check(NEUTRAL).violations


class _WouldPost(Exception):
    pass


@pytest.mark.parametrize("claim", CLAIMS)
def test_poster_refuses_a_pilot_claim_before_the_network(tmp_path, claim):
    import x_poster
    env = {"FDS_X_API_KEY": "k", "FDS_X_API_SECRET": "s", "FDS_X_ACCESS_TOKEN": "t",
           "FDS_X_ACCESS_TOKEN_SECRET": "ts"}
    with patch.dict(os.environ, env, clear=False):
        poster = x_poster.XPoster(account="fds", state_dir=str(tmp_path), verify_links=False)
        with patch.object(poster, "_oauth_post", side_effect=_WouldPost("reached the network call")):
            try:
                poster.post(claim)
            except _WouldPost:
                pytest.fail("XPoster.post sent an unverified pilot claim to the network call")
            except Exception:
                pass            # refused before the network: the promise holds
