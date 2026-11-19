from test_hypotheses import chain, noise, run


def test_services_sorted_and_counted():
    out = run(
        chain()
        + [
            (301, "ERROR svc-a boom request_id=r1"),
            (302, "ERROR gw bad status=503 request_id=r1"),
        ]
    )
    imp = out.impact
    keys = [(s.first_affected, s.service) for s in imp.services]
    assert keys == sorted(keys)
    by = {s.service: s for s in imp.services}
    assert by["svc-a"].error_events == 1 and by["svc-a"].failed_requests == 1
    assert by["gw"].failed_requests == 1
    assert imp.failed_requests == 1


def test_propagation_from_cause_downstream():
    specs = chain() + [
        (310, "WARN gw upstream slow upstream=svc-a status=504 request_id=r2"),
        (340, "WARN gw upstream slow upstream=svc-a status=504 request_id=r3"),
    ]
    imp = run(specs).impact
    pairs = [(p.from_service, p.to_service) for p in imp.propagation]
    assert ("db-x", "svc-a") in pairs and ("svc-a", "gw") in pairs
    assert imp.propagation[0].from_service == "db-x"
    assert all(1 <= len(p.evidence) <= 2 for p in imp.propagation)


def test_empty_impact():
    imp = run(noise(600)).impact
    assert imp.services == () and imp.propagation == () and imp.failed_requests == 0
