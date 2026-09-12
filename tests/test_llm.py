"""Tests for the LLM layer, which needs no API key to be worth testing.

The two things that would quietly invalidate study 6 are a prompt that leaks
the future and a parser that turns a bad reply into a confident number.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wxlab import load_metar
from wxlab.llm import MAX_DELTA, build_prompt, estimate_cost, parse_pmf
from wxlab.model import DeltaModel

DAY = "2026-09-11"


@pytest.fixture(scope="module")
def metar():
    return load_metar()


# ------------------------------------------------------------------ prompt

def test_prompt_contains_no_hour_after_the_decision_hour(metar):
    prompt = build_prompt(metar, DAY, 13)
    for hour in range(14, 24):
        assert f"\n  {hour:02d}:00" not in prompt, f"{hour}:00 leaked into a 13:00 prompt"
    for hour in range(0, 14):
        if metar.get(DAY, hour) is not None:
            assert f"  {hour:02d}:00" in prompt


def test_prompt_withholds_the_year(metar):
    prompt = build_prompt(metar, DAY, 14)
    assert DAY[:4] not in prompt
    assert "September" in prompt and "11" in prompt


def test_prompt_running_max_matches_the_table_it_shows(metar):
    """A truncated table would advertise a max the reader cannot see."""
    for hour in (11, 13, 15):
        prompt = build_prompt(metar, DAY, hour)
        running = metar.running_max(DAY, hour)
        assert f"Running maximum so far today: {running:.0f} C" in prompt
        shown = [float(line.split()[1]) for line in prompt.splitlines()
                 if line.startswith("  ") and ":00" in line.split()[0]]
        assert max(shown) == running


# ------------------------------------------------------------------ parser

@pytest.mark.parametrize("text", [
    '{"0": 0.5, "1": 0.3, "2": 0.15, "3+": 0.05}',
    'Sure.\n```json\n{"0": 0.5, "1": 0.3, "2": 0.15, "3+": 0.05}\n```',
    '{"0": 50, "1": 30, "2": 15, "3+": 5}',  # unnormalised
])
def test_parser_accepts_reasonable_replies(text):
    pmf = parse_pmf(text)
    assert pmf is not None
    assert abs(sum(pmf) - 1) < 1e-9
    assert pmf[0] == pytest.approx(0.5)


@pytest.mark.parametrize("text", [
    "I cannot predict the weather.",
    '{"0": 0.6, "1": 0.4}',            # missing keys -> discard, do not zero-fill
    '{"0": -0.2, "1": 0.5, "2": 0.4, "3+": 0.3}',
    '{"0": 0, "1": 0, "2": 0, "3+": 0}',
    "",
])
def test_parser_rejects_bad_replies(text):
    assert parse_pmf(text) is None


# ------------------------------------------------------------ shared target

def test_baseline_answers_the_same_question(metar):
    """lock_pmf must be a 4-way PMF over the same outcomes the prompt asks for."""
    model = DeltaModel().fit(metar, before=DAY)
    pmf = model.lock_pmf(metar, DAY, 14, cap=MAX_DELTA)
    assert pmf is not None and len(pmf) == MAX_DELTA + 1
    assert abs(sum(pmf) - 1) < 1e-9
    assert all(p >= 0 for p in pmf)


def test_unconditional_control_is_a_real_ablation(metar):
    """use_levels=1 must ignore the conditioning features, not merely blur them."""
    flat = DeltaModel(use_levels=1).fit(metar, before=DAY)
    a = flat.lock_pmf(metar, "2026-09-11", 14, cap=MAX_DELTA)
    b = flat.lock_pmf(metar, "2026-09-10", 14, cap=MAX_DELTA)
    # Same hour, different weather: an hour-only table gives the same shape,
    # so any difference is only the running-max fold, which is a shift of mass
    # onto index 0 and nothing else.
    assert a[1:] == pytest.approx(b[1:], abs=1e-9)


def test_cost_estimate_scales_with_the_run(metar):
    days = metar.days[-10:]
    small = estimate_cost(metar, days[:5], (13, 14))
    large = estimate_cost(metar, days, (13, 14))
    assert large["calls"] > small["calls"] > 0
    assert large["input_tokens"] > small["input_tokens"]
    priced = estimate_cost(metar, days, (13,), {"demo": (3.0, 15.0)})
    assert priced["usd_demo"] > 0
