"""DuckDB schema initialisation — extracted from analytics/data_store.py (store-1)."""

import duckdb


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they do not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol           TEXT   NOT NULL,
            timeframe        TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            symbol        VARCHAR NOT NULL,
            timeframe     VARCHAR NOT NULL,
            strategy      VARCHAR NOT NULL,
            open_time     BIGINT  NOT NULL,
            direction     VARCHAR NOT NULL,
            entry_price   DOUBLE,
            sl_price      DOUBLE,
            reason        VARCHAR,
            confidence    INTEGER,
            fired_at      BIGINT  NOT NULL,
            PRIMARY KEY (symbol, timeframe, strategy, open_time, direction)
        )
    """)
    # Migration: rename legacy signal_outcomes → signal_alert_outcomes.
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if (
        "signal_outcomes" in existing_tables
        and "signal_alert_outcomes" not in existing_tables
    ):
        conn.execute("ALTER TABLE signal_outcomes RENAME TO signal_alert_outcomes")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_alert_outcomes (
            signal_id              TEXT   PRIMARY KEY,
            symbol                 TEXT   NOT NULL,
            tf                     TEXT   NOT NULL,
            strategy               TEXT   NOT NULL,
            direction              TEXT   NOT NULL,
            fired_at_ms            BIGINT NOT NULL,
            candle_ts_ms           BIGINT,
            entry_price            DOUBLE,
            sl_price               DOUBLE,
            tp_price               DOUBLE,
            rr_ratio               DOUBLE,
            confidence_at_fire     INTEGER,
            tags                   TEXT,
            outcome                TEXT,
            outcome_r              DOUBLE,
            outcome_filled_at_ms   BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id               TEXT    PRIMARY KEY,
            symbol               TEXT    NOT NULL,
            timeframe            TEXT    NOT NULL,
            strategy             TEXT    NOT NULL,
            data_start_ms        BIGINT  NOT NULL,
            data_end_ms          BIGINT  NOT NULL,
            days                 INTEGER NOT NULL,
            sl_pct               DOUBLE  NOT NULL,
            tp_r                 DOUBLE  NOT NULL,
            fee_pct              DOUBLE  NOT NULL,
            day_filter           TEXT    NOT NULL,
            total_signals        INTEGER NOT NULL,
            closed_trades        INTEGER NOT NULL,
            win_count            INTEGER NOT NULL,
            loss_count           INTEGER NOT NULL,
            win_rate             DOUBLE  NOT NULL,
            avg_r                DOUBLE  NOT NULL,
            total_r              DOUBLE  NOT NULL,
            max_drawdown_r       DOUBLE  NOT NULL,
            run_at_ms            BIGINT  NOT NULL,
            sweep_id             TEXT,
            long_closed_trades   INTEGER,
            long_win_count       INTEGER,
            long_win_rate        DOUBLE,
            long_avg_r           DOUBLE,
            short_closed_trades  INTEGER,
            short_win_count      INTEGER,
            short_win_rate       DOUBLE,
            short_avg_r          DOUBLE,
            long_total_r         DOUBLE,
            short_total_r        DOUBLE,
            volume_suppress      BOOLEAN
        )
    """)
    # Migration: add long/short split columns to existing DBs.
    existing_bt_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'backtest_runs'"
        ).fetchall()
    }
    # Drop legacy SMT columns (T5b: smt_divergence strategy removed in the
    # equities fork — yfinance OHLCV can't drive SMT pair divergence).
    for legacy_col in ("smt_trend_filter", "secondary_symbol"):
        if legacy_col in existing_bt_cols:
            conn.execute(f"ALTER TABLE backtest_runs DROP COLUMN {legacy_col}")
            existing_bt_cols.discard(legacy_col)
    for col, dtype in [
        ("long_closed_trades", "INTEGER"),
        ("long_win_count", "INTEGER"),
        ("long_win_rate", "DOUBLE"),
        ("long_avg_r", "DOUBLE"),
        ("short_closed_trades", "INTEGER"),
        ("short_win_count", "INTEGER"),
        ("short_win_rate", "DOUBLE"),
        ("short_avg_r", "DOUBLE"),
        ("adr_suppress_threshold", "REAL"),
        ("long_total_r", "DOUBLE"),
        ("short_total_r", "DOUBLE"),
        ("recovery_factor", "DOUBLE"),
        ("volume_suppress", "BOOLEAN"),
    ]:
        if col not in existing_bt_cols:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {col} {dtype}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            trade_id        TEXT    PRIMARY KEY,
            run_id          TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            timeframe       TEXT    NOT NULL,
            strategy        TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            signal_time     BIGINT  NOT NULL,
            entry_time      BIGINT  NOT NULL,
            entry_price     DOUBLE  NOT NULL,
            sl_price        DOUBLE  NOT NULL,
            tp_price        DOUBLE  NOT NULL,
            exit_time       BIGINT,
            exit_price      DOUBLE,
            outcome         TEXT    NOT NULL,
            pnl_r           DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stats_cache (
            symbol        TEXT    NOT NULL,
            days          INTEGER NOT NULL,
            computed_date TEXT    NOT NULL,
            payload_json  TEXT    NOT NULL,
            PRIMARY KEY (symbol, days, computed_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS confidence_ratings (
            config_name   TEXT    NOT NULL,
            strategy      TEXT    NOT NULL,
            tf            TEXT    NOT NULL,
            direction     TEXT    NOT NULL,
            stars         INTEGER NOT NULL,
            avg_r         REAL,
            win_rate      REAL,
            updated_at_ms BIGINT,
            day_filter    TEXT,
            PRIMARY KEY (config_name, strategy, tf, direction)
        )
    """)
    # Migration: add direction column (new PK member) to existing tables.
    existing_cr_cols = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'confidence_ratings'"
        ).fetchall()
    }
    if "direction" not in existing_cr_cols:
        # Recreate table with direction in PK; backfill existing rows as 'combined'.
        conn.execute("""
            CREATE TABLE confidence_ratings_v2 (
                config_name   TEXT    NOT NULL,
                strategy      TEXT    NOT NULL,
                tf            TEXT    NOT NULL,
                direction     TEXT    NOT NULL,
                stars         INTEGER NOT NULL,
                avg_r         REAL,
                win_rate      REAL,
                updated_at_ms BIGINT,
                day_filter    TEXT,
                PRIMARY KEY (config_name, strategy, tf, direction)
            )
        """)
        conn.execute("""
            INSERT INTO confidence_ratings_v2
            SELECT config_name, strategy, tf, 'combined', stars, avg_r, win_rate,
                   updated_at_ms, day_filter
            FROM confidence_ratings
        """)
        conn.execute("DROP TABLE confidence_ratings")
        conn.execute("ALTER TABLE confidence_ratings_v2 RENAME TO confidence_ratings")
    elif "day_filter" not in existing_cr_cols:
        conn.execute("ALTER TABLE confidence_ratings ADD COLUMN day_filter TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_combos (
            combo_id         TEXT    PRIMARY KEY,
            symbol           TEXT    NOT NULL,
            timeframe        TEXT    NOT NULL,
            strategy_a       TEXT    NOT NULL,
            strategy_b       TEXT    NOT NULL,
            window_candles   INTEGER NOT NULL,
            data_start_ms    BIGINT  NOT NULL,
            data_end_ms      BIGINT  NOT NULL,
            days             INTEGER NOT NULL,
            sl_pct           DOUBLE  NOT NULL,
            tp_r             DOUBLE  NOT NULL,
            fee_pct          DOUBLE  NOT NULL,
            day_filter       TEXT    NOT NULL,
            total_signals    INTEGER NOT NULL,
            closed_trades    INTEGER NOT NULL,
            win_count        INTEGER NOT NULL,
            win_rate         DOUBLE  NOT NULL,
            avg_r            DOUBLE  NOT NULL,
            total_r          DOUBLE  NOT NULL,
            max_drawdown_r   DOUBLE  NOT NULL,
            recovery_factor  DOUBLE,
            long_closed_trades  INTEGER,
            long_win_rate    DOUBLE,
            long_avg_r       DOUBLE,
            short_closed_trades INTEGER,
            short_win_rate   DOUBLE,
            short_avg_r      DOUBLE,
            run_at_ms        BIGINT  NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cross_tf_combos (
            combo_id            TEXT    PRIMARY KEY,
            symbol              TEXT    NOT NULL,
            tf_htf              TEXT    NOT NULL,
            tf_ltf              TEXT    NOT NULL,
            strategy_htf        TEXT    NOT NULL,
            strategy_ltf        TEXT    NOT NULL,
            window_hours        DOUBLE  NOT NULL,
            data_start_ms       BIGINT  NOT NULL,
            data_end_ms         BIGINT  NOT NULL,
            days                INTEGER NOT NULL,
            sl_pct              DOUBLE  NOT NULL,
            tp_r                DOUBLE  NOT NULL,
            fee_pct             DOUBLE  NOT NULL,
            day_filter          TEXT    NOT NULL,
            total_signals       INTEGER NOT NULL,
            closed_trades       INTEGER NOT NULL,
            win_count           INTEGER NOT NULL,
            win_rate            DOUBLE  NOT NULL,
            avg_r               DOUBLE  NOT NULL,
            total_r             DOUBLE  NOT NULL,
            max_drawdown_r      DOUBLE  NOT NULL,
            recovery_factor     DOUBLE,
            long_closed_trades  INTEGER,
            long_win_rate       DOUBLE,
            long_avg_r          DOUBLE,
            short_closed_trades INTEGER,
            short_win_rate      DOUBLE,
            short_avg_r         DOUBLE,
            run_at_ms           BIGINT  NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_cache (
            cache_key        TEXT    PRIMARY KEY,
            run_id           TEXT    NOT NULL,
            last_candle_ts   BIGINT  NOT NULL,
            symbol           TEXT    NOT NULL,
            timeframe        TEXT    NOT NULL,
            strategy         TEXT    NOT NULL,
            fee_pct          DOUBLE  NOT NULL,
            n_closed         INTEGER NOT NULL,
            n_long           INTEGER NOT NULL,
            n_short          INTEGER NOT NULL,
            n_win            INTEGER NOT NULL,
            n_loss           INTEGER NOT NULL,
            r_win_rate       DOUBLE  NOT NULL,
            r_avg            DOUBLE  NOT NULL,
            r_total          DOUBLE  NOT NULL,
            n_long_win       INTEGER NOT NULL,
            r_long_win_rate  DOUBLE,
            r_long_avg       DOUBLE,
            r_long_total     DOUBLE  NOT NULL,
            n_short_win      INTEGER NOT NULL,
            r_short_win_rate DOUBLE,
            r_short_avg      DOUBLE,
            r_short_total    DOUBLE  NOT NULL,
            h_median         DOUBLE,
            h_long_median    DOUBLE,
            h_short_median   DOUBLE,
            cached_at_ms     BIGINT  NOT NULL
        )
    """)
    # Backfill existing runs from trades table where split columns are still NULL.
    # Runs after backtest_trades is created so the table always exists.
    conn.execute("""
        UPDATE backtest_runs r
        SET
            long_closed_trades  = t.lct,
            long_win_count      = t.lwc,
            long_win_rate       = CASE WHEN t.lct > 0 THEN t.lwc * 1.0 / t.lct ELSE NULL END,
            long_avg_r          = t.lar,
            short_closed_trades = t.sct,
            short_win_count     = t.swc,
            short_win_rate      = CASE WHEN t.sct > 0 THEN t.swc * 1.0 / t.sct ELSE NULL END,
            short_avg_r         = t.sar
        FROM (
            SELECT
                run_id,
                COUNT(*)   FILTER (WHERE direction = 'long'  AND outcome != 'open') AS lct,
                COUNT(*)   FILTER (WHERE direction = 'long'  AND outcome = 'win')   AS lwc,
                AVG(pnl_r) FILTER (WHERE direction = 'long'  AND outcome != 'open') AS lar,
                COUNT(*)   FILTER (WHERE direction = 'short' AND outcome != 'open') AS sct,
                COUNT(*)   FILTER (WHERE direction = 'short' AND outcome = 'win')   AS swc,
                AVG(pnl_r) FILTER (WHERE direction = 'short' AND outcome != 'open') AS sar
            FROM backtest_trades
            GROUP BY run_id
        ) t
        WHERE r.run_id = t.run_id
          AND r.long_closed_trades IS NULL
    """)
