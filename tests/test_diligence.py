from tradebot import diligence as d


def rep(dim, verdict="pass", claims=()):
    return d.SpecialistReport(dimension=dim, verdict=verdict, summary="", findings=[], claims=list(claims),
                              red_flags=[], unverified=[])


def claim(material=True):
    return d.Claim(statement="s", value="1", source_url="https://x", source_type="sec_filing", material=material)


def verdict(tier="big"):
    return d.Verdict(tier=tier, probability=0.7, expected_move_pct=20, horizon_days=30, direction="long",
                     reasoning="", what_would_upgrade=[], what_would_kill=[])


ALLPASS = [rep(n) for n in d.FULL]


def test_clean_run_keeps_judge_tier():
    assert d.apply_caps(verdict("big"), ALLPASS, d.Verification(checks=[]), [])[0] == "big"


def test_buyer_channel_fail_blocks_trade():
    reps = [rep(n, "fail" if n == "buyer_channel" else "pass") for n in d.FULL]
    tier, why = d.apply_caps(verdict("big"), reps, d.Verification(checks=[]), [])
    assert tier == "watch" and "buyer_channel failed" in why


def test_wrong_material_claim_blocks_trade():
    claims = [claim(True)]
    ver = d.Verification(checks=[d.ClaimCheck(index=0, status="wrong", correct_value="2", note="")])
    assert d.apply_caps(verdict("big"), ALLPASS, ver, claims)[0] == "watch"


def test_many_unverifiable_claims_cap_at_small():
    claims = [claim(False) for _ in range(5)]
    ver = d.Verification(checks=[d.ClaimCheck(index=i, status="unverifiable" if i < 2 else "confirmed",
                                              correct_value="", note="") for i in range(5)])
    assert d.apply_caps(verdict("big"), ALLPASS, ver, claims)[0] == "small"


def test_any_concern_caps_at_standard():
    reps = [rep(n, "concern" if n == "competition" else "pass") for n in d.FULL]
    assert d.apply_caps(verdict("big"), reps, d.Verification(checks=[]), [])[0] == "standard"


def test_pipeline_runs_specialists_in_parallel_and_caps(monkeypatch):
    seen = []
    monkeypatch.setattr(d, "run_specialist", lambda n, b: seen.append(n) or rep(n, "fail" if n == "red_team" else "pass", [claim()]))
    monkeypatch.setattr(d, "verify", lambda cs: d.Verification(checks=[d.ClaimCheck(index=i, status="confirmed", correct_value="", note="") for i in range(len(cs))]))
    monkeypatch.setattr(d, "judge", lambda *a: verdict("big"))
    out = d.run("TICKER X")
    assert sorted(seen) == sorted(d.FULL)
    assert out["final_tier"] == "small" and len(out["claims"]) == len(d.FULL)


def test_fast_lane_runs_three_specialists_and_blocks_unverified_news(monkeypatch):
    seen = []
    monkeypatch.setattr(d, "run_specialist", lambda n, b: seen.append(n) or rep(n, "fail" if n == "news_verification" else "pass"))
    monkeypatch.setattr(d, "verify", lambda cs: d.Verification(checks=[]))
    monkeypatch.setattr(d, "judge", lambda *a: verdict("big"))
    out = d.run("TICKER X", fast=True)
    assert sorted(seen) == sorted(d.FAST)
    assert out["final_tier"] == "watch"
