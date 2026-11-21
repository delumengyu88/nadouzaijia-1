from datetime import datetime

import pytest

from nadou.entitlements import AccessGrants, EntitlementPolicy
from nadou.text import RecordAge

AGES = [29, 30, 89, 90, 179, 180]
TIERS = {
    "free": AccessGrants(),
    "regular": AccessGrants(regular=True),
    "vip": AccessGrants(vip=True),
    "program_90": AccessGrants(program_90=True),
    "program_30": AccessGrants(program_30=True),
    "screenshot": AccessGrants(screenshot=True),
}


def expected(age: int, tier: str) -> str:
    """Permission matrix from the requirements, independent of the policy code."""
    if age < 30:
        return "forensic"
    if tier == "free":
        return "lowres" if age >= 180 else "locked"
    if tier == "vip":
        return "full_download"
    if tier == "regular":
        return "full_download" if age >= 90 else "locked"
    if tier == "program_30":
        return "full_online"
    if tier == "program_90":
        return "full_online" if age >= 90 else "locked"
    if tier == "screenshot":
        if age >= 180:
            return "lowres_plus_shot"
        if age >= 90:
            return "screenshot"
        return "locked"
    raise AssertionError(tier)


def label(decision) -> str:
    if decision.locked and decision.lock_reason == EntitlementPolicy.REASON_FORENSIC:
        return "forensic"
    if decision.locked:
        return "locked"
    if decision.access == "screenshot":
        return "screenshot"
    if decision.access == "lowres" and decision.hd and decision.can_download_screenshot:
        return "lowres_plus_shot"
    if decision.access == "lowres":
        return "lowres"
    if decision.access == "full" and decision.can_download_report:
        return "full_download"
    if decision.access == "full":
        return "full_online"
    raise AssertionError(decision)


@pytest.mark.parametrize("tier", list(TIERS))
@pytest.mark.parametrize("age", AGES)
def test_permission_matrix_boundaries(age, tier):
    decision = EntitlementPolicy.decide(age, TIERS[tier])
    assert decision.age_bucket == EntitlementPolicy.age_bucket(age)
    seen = label(decision)
    assert seen == expected(age, tier)
    if seen in {"locked", "forensic"}:
        assert decision.reveal_fields is False
        assert decision.hd is False
        assert decision.thumbnail is None
        assert decision.can_download_report is False
        assert decision.can_download_screenshot is False
    if seen == "locked" and decision.age_bucket == "d30_89":
        assert decision.lock_reason == "此记录需 VIP 或单节目 30 天档查看"
    if seen == "locked" and decision.age_bucket == "d90_179":
        assert decision.lock_reason == "此记录需普通会员或单节目解锁查看"
    if seen == "forensic":
        assert decision.lock_reason == "有最新记录，可委托取证"
    if seen in {"full_download", "full_online"}:
        assert decision.reveal_fields is True
        assert decision.hd is True
        assert decision.thumbnail == "card"
    if seen == "full_online":
        assert decision.can_download_report is False
        assert decision.can_download_screenshot is False
    if seen == "full_download":
        assert decision.can_download_report is True
        assert decision.can_download_screenshot is True
    if seen == "lowres":
        assert decision.reveal_fields is True
        assert decision.thumbnail == "lowres"
        assert decision.hd is False
        assert decision.can_download_report is False
        assert decision.can_download_screenshot is False
    if seen == "screenshot":
        assert decision.reveal_fields is False
        assert decision.hd is True
        assert decision.thumbnail is None
        assert decision.can_download_report is False
        assert decision.can_download_screenshot is True
    if seen == "lowres_plus_shot":
        assert decision.reveal_fields is True
        assert decision.thumbnail == "lowres"
        assert decision.hd is True
        assert decision.can_download_report is False
        assert decision.can_download_screenshot is True


def test_regular_plus_program_30_download_follows_membership_only():
    grants = AccessGrants(regular=True, program_30=True)
    young = EntitlementPolicy.decide(30, grants)
    assert label(young) == "full_online"
    older = EntitlementPolicy.decide(90, grants)
    assert label(older) == "full_download"
    assert EntitlementPolicy.decide(29, grants).lock_reason == EntitlementPolicy.REASON_FORENSIC


def test_age_rolls_at_beijing_midnight():
    created = datetime(2026, 10, 22, 23, 59, tzinfo=RecordAge.TZ)
    now = datetime(2026, 11, 21, 0, 1, tzinfo=RecordAge.TZ)
    assert RecordAge.days(created, now) == 30
    created = datetime(2026, 10, 23, 0, 0, tzinfo=RecordAge.TZ)
    now = datetime(2026, 11, 21, 0, 0, tzinfo=RecordAge.TZ)
    assert RecordAge.days(created, now) == 29
