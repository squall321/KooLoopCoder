"""LlmConfig.resolve_endpoint — multi-model routing precedence."""

from loopcoder.config import LlmConfig, LlmEndpoint


def _cfg():
    return LlmConfig(
        base_url="http://flat/v1",
        model="flat-model",
        api_key="EMPTY",
        models={
            "fast": LlmEndpoint(base_url="http://127.0.0.1:8001/v1", model="fast"),
            "big": LlmEndpoint(base_url="http://127.0.0.1:8002/v1", model="big", api_key="K"),
        },
        default_model="fast",
    )


def test_explicit_key_routes_to_that_instance():
    assert _cfg().resolve_endpoint("big") == ("http://127.0.0.1:8002/v1", "big", "K")


def test_none_uses_default_model():
    assert _cfg().resolve_endpoint(None) == ("http://127.0.0.1:8001/v1", "fast", "EMPTY")


def test_unknown_falls_back_to_flat_with_literal_name():
    assert _cfg().resolve_endpoint("Qwen/Whatever") == (
        "http://flat/v1",
        "Qwen/Whatever",
        "EMPTY",
    )


def test_no_models_table_is_single_endpoint():
    c = LlmConfig(base_url="http://y/v1", model="solo")
    assert c.resolve_endpoint(None) == ("http://y/v1", "solo", "EMPTY")
    assert c.resolve_endpoint("ignored-literal") == ("http://y/v1", "ignored-literal", "EMPTY")


def test_default_model_not_in_table_falls_back():
    c = LlmConfig(
        base_url="http://flat/v1",
        model="flat",
        models={"a": LlmEndpoint(base_url="http://a/v1", model="a")},
        default_model="missing",
    )
    # default points at a missing key -> flat fallback
    assert c.resolve_endpoint(None) == ("http://flat/v1", "flat", "EMPTY")
