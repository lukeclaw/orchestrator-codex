"""Tests for the provider registry contract."""

import pytest

from orchestrator.providers import (
    CAPABILITY_KEYS,
    DEFAULT_PROVIDER_ID,
    PROVIDERS,
    get_provider,
    list_providers,
)


def test_default_provider_is_claude():
    assert DEFAULT_PROVIDER_ID == "claude"


def test_provider_order_is_stable():
    providers = list_providers()
    assert [provider.id for provider in providers] == ["claude", "codex"]


@pytest.mark.parametrize("provider_id", ["claude", "codex"])
def test_provider_definitions_are_complete(provider_id):
    provider = get_provider(provider_id)
    assert provider.id == provider_id
    assert provider.label
    assert set(provider.capabilities) == set(CAPABILITY_KEYS)


def test_provider_capability_integrity():
    for provider in PROVIDERS.values():
        for capability in provider.capabilities.values():
            if capability.supported:
                assert capability.disabled_reason is None
            else:
                assert capability.disabled_reason


def test_unknown_provider_raises():
    with pytest.raises(KeyError, match="Unknown provider: does-not-exist"):
        get_provider("does-not-exist")
