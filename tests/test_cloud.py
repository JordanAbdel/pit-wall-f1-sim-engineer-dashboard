"""Cloud entitlement — the pure offline-grace decision only (no network)."""
import time

import cloud
from cloud import LICENSE_GRACE_DAYS, grace_verdict

DAY = 86_400
NOW = time.time()


def cache(status="active", age_days=0.0):
    return {"status": status, "checked_at": NOW - age_days * DAY}


def test_no_cache_means_no_grace():
    assert grace_verdict(None, NOW) == (False, 0.0)
    assert grace_verdict({}, NOW) == (False, 0.0)


def test_non_active_status_means_no_grace():
    assert grace_verdict(cache("canceled"), NOW) == (False, 0.0)
    assert grace_verdict(cache("past_due"), NOW) == (False, 0.0)


def test_fresh_cache_grants_grace():
    entitled, left = grace_verdict(cache(age_days=2), NOW)
    assert entitled and left == LICENSE_GRACE_DAYS - 2


def test_stale_cache_denies():
    entitled, left = grace_verdict(cache(age_days=LICENSE_GRACE_DAYS + 1), NOW)
    assert not entitled and left == 0.0


def test_edge_of_grace_window():
    entitled, left = grace_verdict(cache(age_days=LICENSE_GRACE_DAYS - 0.1), NOW)
    assert entitled and 0 < left <= 0.1


def test_unreachable_exception_exists():
    # Unreachable (network) must stay distinguishable from RuntimeError
    # (server said no) — the grace path depends on it.
    assert issubclass(cloud.Unreachable, RuntimeError)
    assert cloud.Unreachable is not RuntimeError
