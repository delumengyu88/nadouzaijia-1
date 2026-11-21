from dataclasses import dataclass


@dataclass(frozen=True)
class AccessGrants:
    """Live grants that apply to one record. Expired rows are already dropped."""

    vip: bool = False
    regular: bool = False
    program_30: bool = False
    program_90: bool = False
    screenshot: bool = False


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
        screenshot_ok = grants.screenshot and age_days >= 90

        if membership_sees or program_sees:
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
