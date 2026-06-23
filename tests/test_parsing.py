"""Regression tests for Arena's text->canonical-variant decoding.

These cover the exact failure inputs that silently corrupt results if the parser
regresses: a letter buried inside a variant label, and a parenthetical label inside
a ranking line. No API calls — run directly (`python tests/test_parsing.py`) or with
pytest.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthsociety.cities.arena import (  # noqa: E402
    _choice_variant, _rank_variant_ids, decode_judgment, analyze_rank, analyze_h2h,
)


def test_choice_explicit_letter():
    order = [{"id": "calm", "label": "Calm"}, {"id": "hype", "label": "Hype"}]
    assert _choice_variant("A", order) == "calm"
    assert _choice_variant("(B)", order) == "hype"
    assert _choice_variant("B.", order) == "hype"


def test_choice_letter_buried_in_label_does_not_false_match():
    # The bug: "Hype Coach" decoded to 'A' via the 'a' in "Coach". Must resolve by label.
    order = [{"id": "hype", "label": "Hype Coach"}, {"id": "straight", "label": "Straight Shooter"}]
    assert _choice_variant("Hype Coach", order) == "hype"
    assert _choice_variant("Straight Shooter", order) == "straight"


def test_choice_counterbalanced_shuffle():
    # Shuffled presentation: B must map to whatever is 2nd in the presented order.
    order = [{"id": "calm", "label": "Calm"}, {"id": "hype", "label": "Hype"}]
    assert decode_judgment(
        {"type": "head_to_head", "variants": order}, order,
        "CHOICE: B\nSTRENGTH: strong\nWHY: energy\nFLIP: nothing")["choice"] == "hype"


def test_rank_parenthetical_labels_do_not_scramble():
    # The bug: "C (Deeper AI coaching) > A ..." scrambled because letters inside the
    # parenthetical bled across slots. Head-split must drop the parenthetical first.
    order = [{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"},
             {"id": "c", "label": "Gamma"}, {"id": "d", "label": "Delta"}, {"id": "e", "label": "Epsilon"}]
    got = _rank_variant_ids("C (Deeper coaching) > A > D > B > E", order)
    assert got == ["c", "a", "d", "b", "e"], got


def test_rank_incomplete_is_not_parse_ok():
    order = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}]
    rec = decode_judgment({"type": "rank", "variants": order}, order,
                          "RANKING: A > B\nTOP_WHY: x\nBOTTOM_WHY: y")  # missing C
    assert rec["parse_ok"] is False
    assert rec["ranking_provided"] == ["a", "b"]


def test_analyze_rank_scores_only_complete_rankings():
    # Borda/avg-rank must ignore incomplete answers so an omitted variant isn't last-placed.
    exp = {"id": "r", "type": "rank",
           "variants": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}]}
    js = [
        {"ranking": ["a", "b", "c"], "parse_ok": True, "top_why": "", "bottom_why": ""},
        {"ranking": ["a", "b"], "parse_ok": False, "top_why": "", "bottom_why": ""},  # excluded
    ]
    out = analyze_rank(exp, js, [])
    assert out["n"] == 1  # only the complete ranking counted


def test_analyze_h2h_collects_flips_and_low_n():
    exp = {"id": "t", "type": "head_to_head",
           "variants": [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}]}
    axes = [{"key": "role", "label": "Role", "values": ["ic", "mgr"]}]
    js = [{"choice": "x", "strength": "strong", "why": "a", "flip": "if it were cheaper", "role": "ic"},
          {"choice": "y", "strength": "lean", "why": "b", "flip": "nothing", "role": "mgr"}]
    out = analyze_h2h(exp, js, axes)
    assert out["flips"] == ["if it were cheaper"]            # "nothing" dropped
    assert out["segments"]["role"]["values"]["ic"]["low_n"] is True  # n=1 flagged


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} parser regression tests passed.")


if __name__ == "__main__":
    _run_all()
