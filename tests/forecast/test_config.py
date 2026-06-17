from analytics.forecast.config import ForecastConfig


def test_default_annualization_is_252() -> None:
    assert ForecastConfig().annualization_days == 252.0


def test_min_history_is_longest_slow_plus_vol_span() -> None:
    cfg = ForecastConfig()  # longest slow 256 + vol_span 32
    assert cfg.min_history == 288
