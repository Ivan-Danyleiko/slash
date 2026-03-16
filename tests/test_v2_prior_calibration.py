from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_v2_priors.py"
    spec = importlib.util.spec_from_file_location("calibrate_v2_priors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recommended_prior_positive_ci_uses_mean() -> None:
    mod = _load_module()
    assert mod._recommended_prior(0.03, 0.01) == 0.03


def test_recommended_prior_non_positive_ci_halves_mean() -> None:
    mod = _load_module()
    assert mod._recommended_prior(0.005, -0.01) == 0.0025


def test_canonical_category_keyword_mapping() -> None:
    mod = _load_module()
    assert mod._canonical_category("2024-us-election", title="Election result in US") == "politics"
    assert mod._canonical_category("ai", title="Bitcoin ETF probability") == "crypto"
