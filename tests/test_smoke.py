"""Import smoke tests for the project skeleton."""

import bizguard


def test_package_can_be_imported() -> None:
    """The src-layout package is importable in the test environment."""
    assert bizguard.ChangeSafetyCard.__name__ == "ChangeSafetyCard"
