"""Cross-platform path policy and auto-registration rejection tests."""

import pytest

from server.services.slug import derive_slug_from_cwd, is_contained_by, path_shape

PATH_SHAPE_CASES = [
    ("/Repo/Child", ("posix-abs", ("Repo", "Child"))),
    ("Repo/Child", ("posix-rel", ("Repo", "Child"))),
    (r"C:\A\b", ("win:c", ("a", "b"))),
    (r"C:repo\child", ("win-rel:c", ("repo", "child"))),
    (r"\\Server\Share\Repo", ("unc:server/share", ("repo",))),
    ("/repo/../other", ("posix-abs", ("repo", "..", "other"))),
]

PATH_EQUAL_CASES = [
    (r"C:\A\b", r"c:\a\B", True),
    ("/A/b", "/a/B", False),
    (r"\\Server\Share\Repo", r"\\server\share\repo", True),
]

CONTAINMENT_CASES = [
    ("/repo/child", "/repo", True),
    ("repo/child", "/repo", False),
    ("/repo/../other", "/repo", False),
    ("/repo/../other/child", "/repo/../other", False),
    (r"C:\A\b\child", r"c:\a\B", True),
    (r"C:\repo\child", "/repo", False),
    (r"C:repo\child", r"C:repo", False),
    (r"\\server\share\repo\child", r"\\SERVER\SHARE\Repo", True),
    (r"\\server\other\repo\child", r"\\server\share\repo", False),
    ("/repo", "/repo", False),
]

DERIVE_CASES = [
    ("/root/project", "project"),
    ("/root", None),
    ("/home/u/projects/real", "real"),
    ("/home/u", None),
    ("/home", None),
    (r"C:\Users\u", None),
    (r"C:\Users\u\projects\real", "real"),
    (r"C:\work\proj\tmp\model_build", None),
    ("/tmp/tmp.ABCD", None),
    ("/work/Temp/model_build", None),
    ("/work/.tmp/model_build", None),
    ("/home/u/.claude/worktrees/agent", None),
    ("/home/u/projects", None),
    ("/home/u/repos", None),
    ("/home/u/workspace", None),
]


@pytest.mark.parametrize(("path", "expected"), PATH_SHAPE_CASES)
def test_path_shape(path, expected):
    assert path_shape(path) == expected


@pytest.mark.parametrize(("left", "right", "expected"), PATH_EQUAL_CASES)
def test_path_shape_equality(left, right, expected):
    assert (path_shape(left) == path_shape(right)) is expected


@pytest.mark.parametrize(("child", "ancestor", "expected"), CONTAINMENT_CASES)
def test_is_contained_by(child, ancestor, expected):
    assert is_contained_by(child, ancestor) is expected


@pytest.mark.parametrize(("cwd", "expected_slug"), DERIVE_CASES)
def test_derive_slug_rejection(cwd, expected_slug):
    slug, reason = derive_slug_from_cwd(cwd)
    assert slug == expected_slug
    assert (reason is None) is (expected_slug is not None)
