import pytest

from nadou.config import get_settings
from nadou.models import User
from nadou.seed import DEMO_VIP_USER_ID, DevSeedDisabled, Seed


def test_dev_seed_defaults_off(monkeypatch):
    monkeypatch.delenv("NADOU_ALLOW_DEV_SEED", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().allow_dev_seed is False
    finally:
        get_settings.cache_clear()


def test_dev_seed_flag_enables_user(monkeypatch):
    monkeypatch.setenv("NADOU_ALLOW_DEV_SEED", "1")
    get_settings.cache_clear()
    try:
        assert get_settings().allow_dev_seed is True
    finally:
        get_settings.cache_clear()


def test_seed_refuses_fixed_vip_user(session):
    with pytest.raises(DevSeedDisabled):
        Seed(session).run()
    assert session.get(User, DEMO_VIP_USER_ID) is None
