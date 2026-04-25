"""Wallet selection policy — Priority 6 Phase A.

Implements a deterministic filter chain for multi-wallet routing:

  1. Ownership   — tenant_id + user_id must match request.
  2. Lifecycle   — wallet must be in "active" state.
  3. Balance     — wallet.balance_usd >= request.required_usd.
  4. Strategy    — wallet's strategy_tags must include request.strategy_tag (or be empty).
  5. Risk gate   — drawdown_pct and exposure_pct within ceiling constants.

Failover path: when no candidate passes filters 4+5 but funded+active candidates
exist, the policy relaxes strategy and risk filters and re-selects from the funded
set. failover_used=True is set on the result to signal this path to the caller.

Ranking among eligible candidates:
  - Primary wallet preferred over secondary.
  - Higher balance_usd preferred when primary flags are equal.
"""
from __future__ import annotations

from typing import Optional, Sequence

import structlog

from server.orchestration.schemas import OrchestrationResult, RoutingRequest, WalletCandidate
from server.schemas.portfolio import MAX_DRAWDOWN, MAX_TOTAL_EXPOSURE_PCT

log = structlog.get_logger(__name__)

_ACTIVE_STATUS: str = "active"
_DRAWDOWN_CEILING: float = MAX_DRAWDOWN          # 0.08
_EXPOSURE_CEILING: float = MAX_TOTAL_EXPOSURE_PCT  # 0.10


class WalletSelectionPolicy:
    """Deterministic wallet selection via an ordered filter chain."""

    def select(
        self,
        request: RoutingRequest,
        candidates: Sequence[WalletCandidate],
    ) -> OrchestrationResult:
        """Run the filter chain and return a routing decision.

        Args:
            request:    Routing requirements from the caller.
            candidates: Pre-fetched wallet candidates from the service layer.

        Returns:
            OrchestrationResult with outcome and selected_wallet_id.
        """
        total = len(candidates)

        # ── Filter 1: empty list ──────────────────────────────────────────────
        if not candidates:
            return OrchestrationResult(
                outcome="no_candidate",
                selected_wallet_id=None,
                reason="candidate list is empty",
                candidates_evaluated=0,
            )

        # ── Filter 2: ownership ───────────────────────────────────────────────
        owned = [
            c for c in candidates
            if c.tenant_id == request.tenant_id and c.user_id == request.user_id
        ]
        if not owned:
            return OrchestrationResult(
                outcome="no_candidate",
                selected_wallet_id=None,
                reason=(
                    f"no candidate matches tenant_id={request.tenant_id!r} "
                    f"user_id={request.user_id!r}"
                ),
                candidates_evaluated=total,
            )

        # ── Filter 3: lifecycle ───────────────────────────────────────────────
        active = [c for c in owned if c.lifecycle_status == _ACTIVE_STATUS]
        if not active:
            return OrchestrationResult(
                outcome="no_active_wallet",
                selected_wallet_id=None,
                reason='no ownership-matched candidate has lifecycle_status="active"',
                candidates_evaluated=total,
            )

        # ── Filter 4: balance ─────────────────────────────────────────────────
        funded = [c for c in active if c.balance_usd >= request.required_usd]
        if not funded:
            return OrchestrationResult(
                outcome="insufficient_balance",
                selected_wallet_id=None,
                reason=f"no active candidate has balance_usd >= {request.required_usd}",
                candidates_evaluated=total,
            )

        # ── Filters 5+6: strategy + risk ──────────────────────────────────────
        eligible = [
            c for c in funded
            if self._strategy_ok(c, request.strategy_tag) and self._risk_ok(c)
        ]

        if eligible:
            best = self._rank(eligible)
            log.info(
                "wallet_selected",
                wallet_id=best.wallet_id,
                strategy_tag=request.strategy_tag,
                failover=False,
            )
            return OrchestrationResult(
                outcome="routed",
                selected_wallet_id=best.wallet_id,
                reason="selected via full filter chain",
                candidates_evaluated=total,
                failover_used=False,
            )

        # ── Failover: relax strategy + risk, keep ownership + lifecycle + balance
        log.warning(
            "wallet_selection_failover",
            funded_count=len(funded),
            strategy_tag=request.strategy_tag,
        )
        best = self._rank(funded)
        return OrchestrationResult(
            outcome="routed",
            selected_wallet_id=best.wallet_id,
            reason="failover — strategy/risk filters relaxed; primary funded wallet selected",
            candidates_evaluated=total,
            failover_used=True,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _strategy_ok(candidate: WalletCandidate, strategy_tag: Optional[str]) -> bool:
        """True when: no strategy requested, wallet has no restrictions, or tag is in wallet set."""
        if strategy_tag is None:
            return True
        if not candidate.strategy_tags:
            return True
        return strategy_tag in candidate.strategy_tags

    @staticmethod
    def _risk_ok(candidate: WalletCandidate) -> bool:
        """True when drawdown and exposure are within ceiling constants."""
        return (
            candidate.drawdown_pct <= _DRAWDOWN_CEILING
            and candidate.exposure_pct < _EXPOSURE_CEILING
        )

    @staticmethod
    def _rank(candidates: list[WalletCandidate]) -> WalletCandidate:
        """Primary first, then descending balance."""
        return sorted(candidates, key=lambda c: (not c.is_primary, -c.balance_usd))[0]
