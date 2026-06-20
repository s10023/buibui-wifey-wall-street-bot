"""Exit-policy research package (exit spec 2026-06-05, parent PR #433).

This PR ships the §2 MFE/MAE excursion diagnostic (`mfe_mae.py`). The §3–§5
policy / replay / A/B layer (parent PR #437) is a deferred follow-up and will
extend these exports.
"""

from analytics.exits.mfe_mae import EXCURSION_COLUMNS, compute_excursions

__all__ = ["EXCURSION_COLUMNS", "compute_excursions"]
