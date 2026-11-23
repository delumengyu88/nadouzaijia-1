from dataclasses import dataclass


@dataclass(frozen=True)
class AccessGrants:
    """Live grants that apply to one record. Expired rows are already dropped."""

    vip: bool = False
    regular: bool = False
    program_30: bool = False
    program_90: bool = False
    screenshot: bool = False
    # Per-program, per-band permanent unlocks bought one at a time.
    band_90_180: bool = False
    band_30_90: bool = False


@dataclass(frozen=True)
class LockedSummary:
    """Counts of records still locked for one caller, and what would open them."""

    locked_total: int
    member: int
    vip: int
    evidence_only: int
    unlockable_total: int
    cheapest_full_unlock: str | None
    unlocks_by_product: dict[str, int]


@dataclass(frozen=True)
class AccessDecision:
    age_bucket: str
    locked: bool
    lock_reason: str | None
    reveal_fields: bool
    thumbnail: str | None
    hd: bool
    can_download_report: bool
    can_download_screenshot: bool
    access: str | None


class EntitlementPolicy:
    """Widest-grant view of one record. Download is decided separately from view.

    Report download follows membership only. Screenshot download follows membership
    or a screenshot grant on that record. A program unlock is online-only.
    Nothing unlocks a record younger than 30 days.
    """

    REASON_FORENSIC = "有最新记录，可委托取证"
    REASON_VIP = "此记录需 VIP 或单节目 30 天档查看"
    REASON_MEMBER = "此记录需普通会员或单节目解锁查看"

    TIER_MEMBER = "member"
    TIER_VIP = "vip"
    TIER_EVIDENCE = "evidence_only"

    PRODUCT_SCREENSHOT = "screenshot"
    PRODUCT_PROGRAM_90 = "single_program_90d"
    PRODUCT_PROGRAM_30 = "single_program_30d"
    PRODUCT_MEMBER_REGULAR = "membership_regular"
    PRODUCT_MEMBER_VIP = "membership_vip"

    @staticmethod
    def age_bucket(age_days: int) -> str:
        if age_days < 30:
            return "lt_30"
        if age_days < 90:
            return "d30_89"
        if age_days < 180:
            return "d90_179"
        return "ge_180"

    @staticmethod
    def decide(age_days: int, grants: AccessGrants) -> AccessDecision:
        bucket = EntitlementPolicy.age_bucket(age_days)
        if age_days < 30:
            return EntitlementPolicy._locked(bucket, EntitlementPolicy.REASON_FORENSIC)

        membership_floor = 30 if grants.vip else 90 if grants.regular else None
        program_floor = 30 if grants.program_30 else 90 if grants.program_90 else None
        membership_sees = membership_floor is not None and age_days >= membership_floor
        program_sees = program_floor is not None and age_days >= program_floor
        band_sees = (bucket == "d90_179" and grants.band_90_180) or (
            bucket == "d30_89" and grants.band_30_90
        )
        screenshot_ok = grants.screenshot and age_days >= 90

        if membership_sees or program_sees or band_sees:
            return AccessDecision(
                age_bucket=bucket,
                locked=False,
                lock_reason=None,
                reveal_fields=True,
                thumbnail="card",
                hd=True,
                can_download_report=membership_sees,
                can_download_screenshot=membership_sees or screenshot_ok,
                access="full",
            )

        if age_days >= 180:
            return AccessDecision(
                age_bucket=bucket,
                locked=False,
                lock_reason=None,
                reveal_fields=True,
                thumbnail="lowres",
                hd=screenshot_ok,
                can_download_report=False,
                can_download_screenshot=screenshot_ok,
                access="lowres",
            )

        if screenshot_ok:
            return AccessDecision(
                age_bucket=bucket,
                locked=False,
                lock_reason=None,
                reveal_fields=False,
                thumbnail=None,
                hd=True,
                can_download_report=False,
                can_download_screenshot=True,
                access="screenshot",
            )

        reason = (
            EntitlementPolicy.REASON_VIP if bucket == "d30_89" else EntitlementPolicy.REASON_MEMBER
        )
        return EntitlementPolicy._locked(bucket, reason)

    @staticmethod
    def summarize_locked(locked_ages: list[int], grants: AccessGrants) -> LockedSummary:
        """Summarize locked rows with a known age. Missing check times are not included."""
        member = vip = evidence = 0
        for age in locked_ages:
            tier = EntitlementPolicy.required_tier(age)
            if tier == EntitlementPolicy.TIER_MEMBER:
                member += 1
            elif tier == EntitlementPolicy.TIER_VIP:
                vip += 1
            elif tier == EntitlementPolicy.TIER_EVIDENCE:
                evidence += 1
        unlockable = member + vip
        if vip:
            cheapest: str | None = EntitlementPolicy.PRODUCT_PROGRAM_30
        elif member:
            cheapest = EntitlementPolicy.PRODUCT_PROGRAM_90
        else:
            cheapest = None
        return LockedSummary(
            locked_total=member + vip + evidence,
            member=member,
            vip=vip,
            evidence_only=evidence,
            unlockable_total=unlockable,
            cheapest_full_unlock=cheapest,
            unlocks_by_product=EntitlementPolicy._offers(member, vip, grants),
        )

    @staticmethod
    def required_tier(age_days: int) -> str | None:
        """Which purchase tier opens a record of this age. None means everyone can see it."""
        if age_days < 30:
            return EntitlementPolicy.TIER_EVIDENCE
        if age_days < 90:
            return EntitlementPolicy.TIER_VIP
        if age_days < 180:
            return EntitlementPolicy.TIER_MEMBER
        return None

    @staticmethod
    def _offers(member: int, vip: int, grants: AccessGrants) -> dict[str, int]:
        sees_90 = grants.vip or grants.regular or grants.program_30 or grants.program_90
        sees_30 = grants.vip or grants.program_30
        catalog = (
            (EntitlementPolicy.PRODUCT_SCREENSHOT, member, sees_90),
            (EntitlementPolicy.PRODUCT_PROGRAM_90, member, sees_90),
            (EntitlementPolicy.PRODUCT_PROGRAM_30, member + vip, sees_30),
            (EntitlementPolicy.PRODUCT_MEMBER_REGULAR, member, grants.regular or grants.vip),
            (EntitlementPolicy.PRODUCT_MEMBER_VIP, member + vip, grants.vip),
        )
        offers: dict[str, int] = {}
        for product, count, covered in catalog:
            if covered or count == 0:
                continue
            offers[product] = count
        return offers

    @staticmethod
    def _locked(bucket: str, reason: str) -> AccessDecision:
        return AccessDecision(
            age_bucket=bucket,
            locked=True,
            lock_reason=reason,
            reveal_fields=False,
            thumbnail=None,
            hd=False,
            can_download_report=False,
            can_download_screenshot=False,
            access=None,
        )
