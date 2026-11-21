import pytest

from nadou.signed_url import FakeSignedUrlProvider


def test_fake_signer_only_returns_derived_urls():
    signer = FakeSignedUrlProvider(now=lambda: 1_700_000_000)
    url = signer.sign_derived("lowres", "rec-1", None)
    assert url.startswith("signed://derived/lowres/rec-1?")
    assert "exp=1700000300" in url
    assert "http://" not in url
    assert "https://" not in url
    with pytest.raises(ValueError):
        signer.sign_derived("original", "rec-1", "user")
