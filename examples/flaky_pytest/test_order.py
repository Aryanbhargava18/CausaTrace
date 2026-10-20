"""A small pytest failure caused by relying on hash-dependent set order."""


def test_priority_order_is_stable() -> None:
    values = {"cat", "dog"}
    assert tuple(values) == ("cat", "dog")
