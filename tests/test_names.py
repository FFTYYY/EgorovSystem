import pytest

from egorovsystem.names import find_references, is_valid_name, normalize_scopes, scope_allows, substitute


def test_names():
    assert is_valid_name("openai_keys")
    assert is_valid_name("aws.prod-1")
    for bad in ("", ".hidden", "-x", "a b", "a/b", "x" * 129, None):
        assert not is_valid_name(bad)


def test_scopes():
    assert scope_allows(["*"], "variable", "anything")
    assert scope_allows(["variable:openai_*"], "variable", "openai_keys")
    assert not scope_allows(["variable:openai_*"], "function", "openai_keys")
    assert not scope_allows(["variable:openai_*"], "variable", "aws_access")
    assert scope_allows(["function:*", "variable:aws_*"], "variable", "aws_access")
    assert normalize_scopes(None) == ["*"]
    assert normalize_scopes([" a* ", "a*"]) == ["a*"]
    for bad in ([], ["x y"], ["kind:x"], "*", [1]):
        with pytest.raises(ValueError):
            normalize_scopes(bad)


def test_substitution_matches_old_semantics():
    values = {"token": "abc", "tricky": "${token}"}
    content = "curl -H ${token} ${HOME} ${tricky} $token ${token}"
    assert find_references(content) == ["token", "HOME", "tricky"]
    assert substitute(content, values.get) == "curl -H abc ${HOME} ${token} $token abc"
