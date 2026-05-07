# WARP•SENTINEL Report — P3d Signal Scan Loop + Execution Queue

Branch: WARP/CRUSADERBOT-P3D-SIGNAL-SCAN-EXECUTION-QUEUE
Head SHA: 6c2183d4b5c35663dc67e15e058c1071f61db455
Date: 2026-05-07 22:30 Asia/Jakarta
Tier: MAJOR
Environment: staging / paper-trading

---

## 1. Test Plan

Phases 0–5 per CLAUDE.md WARP•SENTINEL protocol.
Scope: P3d signal_following scan loop + execution queue + subscribe/unsubscribe
enrollment + migrations 011/012. Not in scope: live trading, R12 Fly.io
deployment, per-user MarketFilters, execution_queue retry policy.

---

## 2. Environment

| Component | Status |
|---|---|
| Infra (DB pool, scheduler) | ENFORCED (staging) |
| Risk gate | ENFORCED |
| Telegram | warn-only (paper) |
| Activation guards | NOT SET — paper-only validation |

---

## 3. Phase 0 — Pre-Test

PASS — all gates clear.

- Forge report: projects/polymarket/crusaderbot/reports/forge/p3d-signal-scan-execution-queue.md ✓
- All 6 sections present: What Was Built / Architecture / Files / Working / Known Issues / What Is Next ✓
- Branch: WARP/CRUSADERBOT-P3D-SIGNAL-SCAN-EXECUTION-QUEUE ✓ (exact match)
- Head SHA: 6c2183d4b5c35663dc67e15e058c1071f61db455 ✓ (matches task)
- No phase*/ folders: confirmed via find — none ✓
- Domain structure: services/signal_scan/ created under project root ✓
- PR body updated: prerequisite PR #895 (d25f4fda) merged, no duplicate STRATEGY_AVAILABILITY entry ✓
- CI: Lint + Test ✓, Trigger WARP CMD Gate ✓ (all 3 check runs passed)
- PROJECT_STATE.md: minor drift (test count 459→463, timestamp stale) — corrected in this commit

---

## 4. Findings

### Phase 1 — Functional

PASS — all claimed behaviors verified against code evidence.

**Enrollment query** (signal_scan_job.py:60–95):
- Queries user_strategies WHERE strategy_name='signal_following' AND enabled=TRUE ✓
- Joins users, wallets, user_settings, user_risk_profile — no sub_accounts (removed) ✓
- Filters: access_tier>=3, auto_trade_on=TRUE, paused=FALSE ✓

**_process_candidate pipeline** (signal_scan_job.py:270–394) — mandatory ordering:

1. Permanent dedup: _publication_already_queued → skip if pub_uuid in execution_queue (line 292) ✓
2. Market lookup: _load_market → skip if None, log "skipped_market_not_synced" (line 303) ✓
3. Gate context build: side normalized lowercase, side-aware price via is-not-None (line 237) ✓
4. risk_evaluate(gate_ctx) — MANDATORY (line 320) ✓
5. Gate rejection: returns before any insert or execute (line 325–332) ✓ — router unreachable
6. _insert_execution_queue ON CONFLICT DO NOTHING (line 338) ✓
7. Concurrent dedup: `if not inserted: return` before router_execute (line 353) ✓
8. router_execute — called ONLY after gate approval + insert success (line 362) ✓
9. _mark_executed / _mark_failed (line 380 / 390) ✓

**Side normalization** (signal_scan_job.py:277): `side = cand.side.lower()` — signal_evaluator.py:302
produces uppercase; normalized once, propagated to key, queue, router ✓

**Price selection** (signal_scan_job.py:238–244): NO→no_price, YES→yes_price;
is-not-None guards preserve cached 0.0 price; cross-fallback when primary is None ✓

**Allocation default** (signal_scan_job.py:197): `_alloc if _alloc is not None else 0.10`
— weight=0 preserved (is-not-None, not or-falsy) ✓

**STRATEGY_AVAILABILITY** (domain/risk/constants.py): "signal_following":
["conservative","balanced","aggressive"] ✓ — gate step 4 enforces this dict ✓

**Scheduler** (scheduler.py:460–461): sf_scan_job.run_once, id="signal_following_scan",
max_instances=1, coalesce=True — separate from legacy run_signal_scan ✓

**Subscribe enrollment** (signal_feed_service.py):
- New sub: upserts user_strategies ON CONFLICT DO UPDATE SET enabled=TRUE ✓
- Existing sub ("exists" path): same upsert in early-return branch — covers pre-existing subscribers ✓
- Unsubscribe: counts remaining active subs; disables user_strategies if remaining=0 ✓

**Migrations**:
- 011_execution_queue.sql: CREATE TABLE IF NOT EXISTS + UNIQUE partial index (user_id, publication_id) WHERE publication_id IS NOT NULL + 2 supporting indexes; idempotent ✓
- 012_backfill_signal_following_strategy.sql: INSERT DISTINCT active subscribers ON CONFLICT DO UPDATE; idempotent ✓

### Phase 2 — Test Evidence

PASS — 463/463 green (CI confirmed, local verified).

Key coverage mapped to runtime claims:
- test_strategy_availability_includes_signal_following / test_signal_following_matches_strategy_name ✓
- test_build_user_context_* (happy path, clamp 0–1, sub_account fallback) ✓
- test_build_idempotency_key_* (determinism, side difference, pub difference, sf: prefix) ✓
- test_insert_execution_queue_returns_true_on_new / _false_on_conflict ✓
- test_process_candidate_skips_when_already_queued (permanent dedup) ✓
- test_process_candidate_skips_when_market_not_synced ✓
- test_process_candidate_logs_rejection_and_does_not_execute (gate mandatory) ✓
- test_process_candidate_happy_path_inserts_queue_and_executes ✓
- test_process_candidate_marks_failed_when_router_raises ✓
- test_process_candidate_skips_when_concurrent_insert_conflict ✓
- test_run_once_scan_failure_does_not_stop_other_users (per-user isolation) ✓
- test_subscribe_upserts_user_strategies_when_already_subscribed (exists-path fix) ✓
- test_subscribe_upserts_user_strategies_on_success ✓
- test_unsubscribe_disables_strategy_when_last_subscription ✓
- test_unsubscribe_keeps_strategy_enabled_when_other_subs_remain ✓

### Phase 3 — Failure Modes

PASS with one documented limitation.

| Scenario | Handling | Location |
|---|---|---|
| DB down on load_users | Caught, logged, returns | signal_scan_job.py:408–411 |
| Per-user scan exception | Caught, logged, continues | signal_scan_job.py:426–430 |
| Per-candidate unhandled | Caught, logged | signal_scan_job.py:438–446 |
| Risk gate rejection | Logged, early return | signal_scan_job.py:325–332 |
| Router raises | Caught, _mark_failed | signal_scan_job.py:387–393 |
| _mark_failed DB error | Caught in inner try/except | signal_scan_job.py:390–393 |
| Market not synced | skipped_market_not_synced | signal_scan_job.py:304–306 |
| Duplicate pub (permanent) | execution_queue UNIQUE index | migration 011 |
| Concurrent insert | ON CONFLICT DO NOTHING | signal_scan_job.py:338–351 |
| Strategy not registered | KeyError caught, logged | signal_scan_job.py:418–420 |

Documented limitation (forge report, not a defect): execution_queue 'failed' rows have
no retry policy — permanent failure on router raise. Future lane. Not in scope P3d.

### Phase 4 — Async Safety

PASS.

- No import threading in signal_scan_job.py ✓
- No blocking HTTP in scan: _load_market / _load_enrolled_users are asyncpg only ✓
- SignalFollowingStrategy.scan() pure DB reads (P3c SENTINEL 100/100 baseline) ✓
- AsyncIOScheduler max_instances=1 — single concurrent tick enforced ✓
- No shared mutable state between user iterations ✓
- UserContext / MarketFilters instantiated fresh per iteration ✓

### Phase 5 — Risk Rules

PASS — all hard rules enforced.

| Rule | Location | Status |
|---|---|---|
| Risk gate mandatory before queue insert | signal_scan_job.py:319–332 | ✓ ENFORCED |
| Risk gate mandatory before router_execute | signal_scan_job.py:353–379 | ✓ ENFORCED |
| Kelly a=0.25 hard cap | gate.py:270, constants.py:KELLY_FRACTION=0.25 | ✓ ENFORCED |
| Max position <= 10% | gate.py:271–275, MAX_POSITION_PCT=0.10 | ✓ ENFORCED |
| Activation guards NOT SET | No mutation in any new file | ✓ CONFIRMED |
| Live guard mandatory | gate.py:280, _passes_live_guards | ✓ ENFORCED |
| Paper fallback when guards unset | gate.py:281–298 | ✓ ENFORCED |
| No ENABLE_LIVE_TRADING bypass | No bypass path found | ✓ CONFIRMED |

---

## 5. Critical Issues

None found.

---

## 6. Stability Score

| Component | Weight | Score | Notes |
|---|---|---|---|
| Architecture | 20% | 20/20 | Registry-based, dual-layer dedup, correct pipeline separation |
| Functional | 20% | 20/20 | All claimed behaviors verified; enrollment fix complete |
| Failure modes | 20% | 18/20 | Comprehensive isolation; retry absent (documented, not a defect) |
| Risk rules | 20% | 20/20 | Kelly capped, no bypass, activation guards NOT SET confirmed |
| Infra + Telegram | 10% | 9/10 | Scheduler wired; migration 012 backfill handles existing subscribers |
| Latency | 10% | 9/10 | Asyncio only, no HTTP in scan; no regression vs P3c baseline |

**Total: 96/100**

---

## 7. GO-LIVE Status

**APPROVED — Score 96/100. Zero critical issues.**

Merge gate conditions (all satisfied for paper/staging):

- STRATEGY_AVAILABILITY["signal_following"] exact match ✓ (PR #895 merged prerequisite)
- Risk gate mandatory — verified no execution path bypasses it ✓
- execution_queue UNIQUE index prevents re-execution across scan ticks ✓
- subscribe/unsubscribe user_strategies enrollment wired ✓
- Migration 012 backfill handles pre-existing active subscribers ✓
- Activation guards NOT SET, no mutations in new code ✓
- Kelly unchanged at 0.25 ✓
- 463/463 tests green ✓

Deferred (not blocking merge):
- execution_queue retry policy for 'failed' rows (future lane)
- Per-user MarketFilters configuration (future lane)
- F401 ruff cleanup (LOW, known issue)

Live activation remains gated on EXECUTION_PATH_VALIDATED + CAPITAL_MODE_CONFIRMED
+ ENABLE_LIVE_TRADING — NOT SET, NOT in scope for P3d.

---

## 8. Fix Recommendations

None required. Zero critical issues.

All P1/P2 items raised by Codex during PR review were resolved before SENTINEL:

- P1 side normalization — resolved caa6c5d ✓
- P1 NO price selection — resolved caa6c5d ✓
- P2 sub_accounts JOIN (not in migrations/) — resolved caa6c5d ✓
- P1 user enrollment gap (subscribe never wrote user_strategies) — resolved 35b6392 ✓
- P1 existing subscriber backfill (exists-path missed, no migration) — resolved 4e0fd6f ✓
- P2 zero price preservation (or-falsy discarded 0.0) — resolved a62809f ✓
- P2 zero weight preservation (or-falsy discarded weight=0) — resolved 6c2183d ✓

---

## 9. Telegram Preview

P3d does not add new Telegram commands or user-facing alerts. Scan outcomes are
internal structured JSON logs (structlog). Existing operator commands apply:

- /status — system health + activation guard state
- /kill — kill switch (halts all execution including signal_following_scan tick)
- /jobs — APScheduler job monitor (job id "signal_following_scan" visible)

Alert format (existing pipeline, paper mode):
```
📊 Signal Scan — accepted
User: <user_id>  Market: <market_id>  Side: yes/no
Size: $X.XX USDC  Mode: paper
```

---

Validation Tier: MAJOR
Claim Level: FULL RUNTIME INTEGRATION (scoped P3d — signal_following scan + execution queue + risk gate wiring)
Validated by: WARP•SENTINEL
NEXT GATE: Return to WARP🔹CMD for merge decision → R12 final Fly.io deployment
