"""Keep the installed client's path policy in parity with the server."""

import pytest
from hydra_cli.paths import (
    derive_slug_from_cwd,
    is_contained_by,
    is_rejected_path,
    path_shape,
)

from tests.test_slug_stoplist import (
    CONTAINMENT_CASES,
    DERIVE_CASES,
    PATH_EQUAL_CASES,
    PATH_SHAPE_CASES,
)


@pytest.mark.parametrize(("path", "expected"), PATH_SHAPE_CASES)
def test_client_path_shape(path, expected):
    assert path_shape(path) == expected


@pytest.mark.parametrize(("left", "right", "expected"), PATH_EQUAL_CASES)
def test_client_path_shape_equality(left, right, expected):
    assert (path_shape(left) == path_shape(right)) is expected


@pytest.mark.parametrize(("child", "ancestor", "expected"), CONTAINMENT_CASES)
def test_client_is_contained_by(child, ancestor, expected):
    assert is_contained_by(child, ancestor) is expected


@pytest.mark.parametrize(("cwd", "expected_slug"), DERIVE_CASES)
def test_client_derive_and_rejection(cwd, expected_slug):
    slug, reason = derive_slug_from_cwd(cwd)
    assert slug == expected_slug
    assert (reason is None) is (expected_slug is not None)
    assert is_rejected_path(cwd) is (expected_slug is None)
