"""Server-side membership prices. Callers never supply the amount."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PayProduct:
    code: str
    title: str
    membership: str
    duration_days: int
    amount_yuan: int
    amount_fen: int
    test_only: bool = False

    @property
    def description(self) -> str:
        return f"{self.title} {self.duration_days} 天"

    def public(self) -> dict:
        return {
            "id": self.code,
            "title": self.title,
            "membership": self.membership,
            "duration_days": self.duration_days,
            "amount_fen": self.amount_fen,
            "amount_yuan": f"{self.amount_fen // 100}.{self.amount_fen % 100:02d}",
        }


REGULAR = PayProduct("membership_regular", "普通会员", "regular", 30, 299, 29900)
VIP = PayProduct("membership_vip", "VIP", "vip", 30, 599, 59900)
# One day of 普通会员 so an experience-version payment can run the grant path.
TEST = PayProduct("membership_test", "支付测试", "regular", 1, 0, 1, test_only=True)

# Workbench membership conversion and revenue skip these.
TEST_PRODUCTS = frozenset({TEST.code, "membership_dev"})

_PRODUCTS = (REGULAR, VIP, TEST)
_BY_CODE = {product.code: product for product in _PRODUCTS}


def products_for(test_enabled: bool) -> tuple[PayProduct, ...]:
    if test_enabled:
        return _PRODUCTS
    return tuple(product for product in _PRODUCTS if not product.test_only)


def product_for_sale(code: str, *, test_enabled: bool) -> PayProduct | None:
    product = _BY_CODE.get(code)
    if product is None or (product.test_only and not test_enabled):
        return None
    return product


def product_by_code(code: str | None) -> PayProduct | None:
    if code is None:
        return None
    return _BY_CODE.get(code)


def excluded_from_workbench_stats(product: str | None) -> bool:
    """True for test SKUs. Formal orders and legacy rows with no product stay in the stats."""
    return product in TEST_PRODUCTS
