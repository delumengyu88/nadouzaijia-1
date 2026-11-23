"""Age bands on the result page: who sees which band, and what opens a locked one.

180天以上 is open to everyone (free users get a blurred picture only).
90–180天 opens with 普通会员, VIP, or a one-program unlock of that band.
30–90天 opens with VIP or a one-program unlock of that band.
30天内 never shows details: only the count and the 委托取证 entry.
体验会员 grants a VIP entitlement, so it sees what VIP sees.
"""

from dataclasses import dataclass

from nadou.entitlements import AccessDecision, AccessGrants

# Ordered as the result page draws them: oldest first.
BAND_ORDER = ("ge_180", "d90_179", "d30_89", "lt_30")

BAND_LABELS = {
    "ge_180": "180天以上",
    "d90_179": "90–180天",
    "d30_89": "30–90天",
    "lt_30": "30天内",
}

# Spaced form used inside the Excel report, e.g. 「90–180 天 · 12 条 · 待解锁」.
BAND_REPORT_LABELS = {
    "ge_180": "180 天以上",
    "d90_179": "90–180 天",
    "d30_89": "30–90 天",
    "lt_30": "30 天内",
}

# Entitlement kinds. One row per user, program and band; it never expires in practice.
KIND_BAND_90_180 = "band_90_180"
KIND_BAND_30_90 = "band_30_90"
BAND_KINDS = frozenset({KIND_BAND_90_180, KIND_BAND_30_90})

# Pay product codes. WeChat item ids come from NADOU_XPAY_PRODUCTS.
PRODUCT_BAND_90_180 = "unlock_band_90_180"
PRODUCT_BAND_30_90 = "unlock_band_30_90"

BAND_PRODUCT = {"d90_179": PRODUCT_BAND_90_180, "d30_89": PRODUCT_BAND_30_90}
BAND_KIND = {"d90_179": KIND_BAND_90_180, "d30_89": KIND_BAND_30_90}
PRODUCT_BAND = {code: band for band, code in BAND_PRODUCT.items()}

# Probe ages used to ask the policy about a whole band.
_PROBE_AGE = {"ge_180": 400, "d90_179": 120, "d30_89": 60, "lt_30": 0}

UPGRADE_VIP = "升级 VIP 可查看全部 30 天以上记录"
UPGRADE_MEMBER = "开通会员可查看全部 30 天以上记录"
EVIDENCE_NOTE = "30 天内只显示条数和委托取证入口，不展示明细"


def viewer_tier(grants: AccessGrants) -> str:
    if grants.vip:
        return "vip"
    if grants.regular:
        return "regular"
    return "free"


def frame_view(decision: AccessDecision) -> str | None:
    """What the frame endpoint returns for this decision. The client never chooses."""
    if decision.locked:
        return None
    if decision.access == "full":
        return "clear"
    if decision.access == "lowres":
        return "blur"
    return None


def band_state(band: str, grants: AccessGrants) -> tuple[str, str | None]:
    """(state, frame view) for one band. state is open, locked or evidence."""
    from nadou.entitlements import EntitlementPolicy

    if band == "lt_30":
        return "evidence", None
    decision = EntitlementPolicy.decide(_PROBE_AGE[band], grants)
    if decision.locked or not decision.reveal_fields:
        return "locked", None
    return "open", frame_view(decision)


@dataclass(frozen=True)
class BandOffer:
    product: str
    amount_fen: int
    purchasable: bool

    @property
    def price_text(self) -> str:
        yuan, fen = divmod(self.amount_fen, 100)
        if fen == 0:
            return f"¥{yuan}"
        return f"¥{yuan}.{fen:02d}".rstrip("0")


def band_offer(band: str, grants: AccessGrants, *, can_buy: bool) -> BandOffer | None:
    """The single unlock that opens this locked band, if one is sold for it."""
    from nadou.payments.catalog import product_by_code
    from nadou.payments.config import live_virtual_product_id

    code = BAND_PRODUCT.get(band)
    if code is None:
        return None
    product = product_by_code(code)
    if product is None:
        return None
    ready = can_buy and live_virtual_product_id(code) is not None
    return BandOffer(product=code, amount_fen=product.amount_fen, purchasable=ready)


def upgrade_hint(band: str, grants: AccessGrants) -> str | None:
    tier = viewer_tier(grants)
    if band == "d30_89" and tier == "regular":
        return UPGRADE_VIP
    return None


def band_counts(buckets: list[str]) -> dict[str, int]:
    counts = dict.fromkeys(BAND_ORDER, 0)
    for bucket in buckets:
        if bucket in counts:
            counts[bucket] += 1
    return counts
