-- 012_backfill_signal_following_strategy.sql
-- Backfill user_strategies enrollment for all existing active signal_following
-- subscribers who predate the subscribe/unsubscribe enrollment fix (P3d).
--
-- Any user with at least one active user_signal_subscriptions row should have
-- an enabled user_strategies row so the signal_following scan loop picks them
-- up.  ON CONFLICT ensures this is a no-op for users already enrolled.
--
-- Idempotency: safe to re-run on startup.

INSERT INTO user_strategies (user_id, strategy_name, weight, enabled)
SELECT DISTINCT user_id, 'signal_following', 1.0, TRUE
  FROM user_signal_subscriptions
 WHERE unsubscribed_at IS NULL
ON CONFLICT (user_id, strategy_name) DO UPDATE SET enabled = TRUE;
