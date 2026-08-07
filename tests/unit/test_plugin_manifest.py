import json
from importlib import resources

import pytest

from dkg.core.errors import ValidationError
from dkg.plugins.manifest import validate_manifest


def test_example_manifest_valid():
    raw = json.loads(resources.files("dkg.plugins").joinpath("example.json").read_text(encoding="utf-8"))
    validate_manifest(raw)


def test_missing_required_key():
    with pytest.raises(ValidationError):
        validate_manifest({"name": "x", "version": "0.1.0"})  # missing capabilities


def test_bad_name_pattern():
    with pytest.raises(ValidationError):
        validate_manifest({"name": "Bad Name!", "version": "0.1.0", "capabilities": ["x"]})


def test_bad_version_pattern():
    with pytest.raises(ValidationError):
        validate_manifest({"name": "ok", "version": "abc", "capabilities": ["x"]})


def test_unexpected_key():
    with pytest.raises(ValidationError):
        validate_manifest(
            {"name": "ok", "version": "0.1.0", "capabilities": ["x"], "extra": True}
        )
