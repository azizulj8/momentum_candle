# =============================================================================
# backtest.py — Backtesting Strategi Momentum Candle M1 (XAUUSD)
# =============================================================================
# Cara pakai:
#   python backtest.py
# Atau dengan argumen:
#   python backtest.py --days 90 --rr 1.5 --body_mult 1.2 --retrace 0.38
# =============================================================================

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

try:
    import MetaTrader5 as mt5
    HAS_MT5 = True
except ImportError:
    HAS_MT5 = False

# Import modul internal
import config as cfg
from candle_detector import (
    add_candle_metrics, calculate_atr, calculate_ema,
    detect_marubozu_bullish, detect_bullish_impulse, detect_hammer,
    detect_marubozu_bearish, detect_bearish_impulse, detect_shooting_star,
)


# =============================================================================
# KONFIGURASI BACKTEST (bisa di-override lewat argumen CLI)
# =============================================================================

@dataclass
class BacktestConfig:
    # Data
    symbol        : str   = cfg.SYMBOL
    days          : int   = 60        # Berapa hari history yang diuji
    timeframe_str : str   = "M1"

    # Parameter Strategi (diambil dari config, bisa di-override)
    atr_period        : int   = cfg.ATR_PERIOD
    ema_period        : int   = cfg.EMA_PERIOD
    use_ema_filter    : bool  = cfg.USE_EMA_FILTER
    marubozu_body_mult: float = cfg.MARUBOZU_BODY_MULT
    impulse_body_mult : float = cfg.IMPULSE_BODY_MULT
    marubozu_max_wick : float = cfg.MARUBOZU_MAX_WICK
    impulse_max_wick  : float = cfg.IMPULSE_MAX_WICK
    pinbar_min_wick   : float = cfg.PINBAR_MIN_WICK
    pinbar_max_opp_wick: float = cfg.PINBAR_MAX_OPP_WICK

    retrace_ratio     : float = cfg.RETRACE_RATIO
    sl_buffer_pips    : float = cfg.SL_BUFFER_PIPS
    rr_ratio          : float = cfg.RR_RATIO
    order_expiry_candles: int = 1     # Limit order aktif selama N candle

    # Simulasi
    initial_balance : float = 10_000.0
    lot_size        : float = 0.01
    pip_size        : float = 0.10    # XAUUSD: 1 pip = $0.10 per 0.01 lot


# =============================================================================
# BAGIAN 1: AMBIL DATA HISTORIS
# =============================================================================

TIMEFRAME_MAP_BT = {
    "M1" : 1, "M5": 5, "M15": 15, "H1": 60,
}

def fetch_history_mt5(cfg_bt: BacktestConfig) -> Optional[pd.DataFrame]:
    """Ambil data historis dari MT5."""
    if not HAS_MT5:
        print("[ERROR] MetaTrader5 library tidak tersedia.")
        return None

    tf_map = {
        "M1" : mt5.TIMEFRAME_M1,
        "M5" : mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1" : mt5.TIMEFRAME_H1,
    }
    tf = tf_map.get(cfg_bt.timeframe_str, mt5.TIMEFRAME_M1)

    if not mt5.initialize():
        print(f"[ERROR] MT5 gagal init: {mt5.last_error()}")
        return None

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=cfg_bt.days)

    print(f"[INFO] Mengambil data {cfg_bt.symbol} {cfg_bt.timeframe_str} "
          f"dari {utc_from.date()} s/d {utc_to.date()}...")

    rates = mt5.copy_rates_range(cfg_bt.symbol, tf, utc_from, utc_to)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(f"[ERROR] Tidak ada data: {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.columns = [c.lower() for c in df.columns]
    print(f"[INFO] Total candle didapat: {len(df):,}")
    return df


def load_csv(filepath: str) -> Optional[pd.DataFrame]:
    """Alternatif: load dari file CSV jika MT5 tidak tersedia."""
    try:
        df = pd.read_csv(filepath, parse_dates=['time'], index_col='time')
        df.columns = [c.lower() for c in df.columns]
        print(f"[INFO] CSV loaded: {len(df):,} candle dari {filepath}")
        return df
    except Exception as e:
        print(f"[ERROR] Gagal load CSV: {e}")
        return None


# =============================================================================
# BAGIAN 2: ENGINE BACKTESTING
# =============================================================================

@dataclass
class Trade:
    """Representasi satu trade dalam backtest."""
    idx          : int
    time_signal  : pd.Timestamp
    candle_type  : str
    signal       : str       # "BUY" atau "SELL"
    entry        : float
    sl           : float
    tp           : float
    lot          : float
    time_entry   : Optional[pd.Timestamp] = None
    time_exit    : Optional[pd.Timestamp] = None
    exit_price   : float = 0.0
    outcome      : str   = ""   # "TP", "SL", "EXPIRED", "OPEN"
    pnl_pips     : float = 0.0
    pnl_usd      : float = 0.0


def _detect_candle_type(row: pd.Series, atr: float, cfg_bt: BacktestConfig) -> tuple[str, str]:
    """
    Mendeteksi tipe candle dan arah sinyal.
    Returns: (candle_type, signal) atau ("", "")
    """
    # Buat objek config sederhana untuk kompatibilitas candle_detector
    class _Cfg:
        MARUBOZU_BODY_MULT  = cfg_bt.marubozu_body_mult
        IMPULSE_BODY_MULT   = cfg_bt.impulse_body_mult
        MARUBOZU_MAX_WICK   = cfg_bt.marubozu_max_wick
        IMPULSE_MAX_WICK    = cfg_bt.impulse_max_wick
        PINBAR_MIN_WICK     = cfg_bt.pinbar_min_wick
        PINBAR_MAX_OPP_WICK = cfg_bt.pinbar_max_opp_wick

    c = _Cfg()

    # BUY signals
    if detect_marubozu_bullish(row, atr, c):
        return "Marubozu Bullish", "BUY"
    if detect_bullish_impulse(row, atr, c):
        return "Bullish Impulse", "BUY"
    if detect_hammer(row, c):
        return "Hammer", "BUY"

    # SELL signals
    if detect_marubozu_bearish(row, atr, c):
        return "Marubozu Bearish", "SELL"
    if detect_bearish_impulse(row, atr, c):
        return "Bearish Impulse", "SELL"
    if detect_shooting_star(row, c):
        return "Shooting Star", "SELL"

    return "", ""


def run_backtest(df: pd.DataFrame, cfg_bt: BacktestConfig) -> list[Trade]:
    """
    Engine utama backtesting.

    Algoritma per candle:
    1. Kalkulasi ATR & EMA
    2. Deteksi tipe candle sinyal pada candle[i]
    3. Hitung harga Limit Order (Entry, SL, TP)
    4. Simulasikan apakah candle[i+1] menyentuh Entry (Limit Order terisi)
    5. Jika terisi, pantau candle berikutnya sampai TP atau SL tercapai
    6. Jika tidak terisi dalam ORDER_EXPIRY_CANDLES → EXPIRED
    """
    # Persiapkan data
    df = add_candle_metrics(df.copy())
    df['atr'] = calculate_atr(df, cfg_bt.atr_period)
    df['ema'] = calculate_ema(df, cfg_bt.ema_period)

    trades = []
    n      = len(df)
    warmup = max(cfg_bt.atr_period, cfg_bt.ema_period) + 5

    print(f"[INFO] Mulai simulasi backtest pada {n:,} candle (warmup: {warmup})...")

    i = warmup
    while i < n - 2:
        row = df.iloc[i]
        atr = df['atr'].iloc[i]
        ema = df['ema'].iloc[i]

        if pd.isna(atr) or atr == 0:
            i += 1
            continue

        # Filter tren EMA
        price = row['close']
        if cfg_bt.use_ema_filter:
            allow_buy  = price > ema
            allow_sell = price < ema
        else:
            allow_buy = allow_sell = True

        candle_type, signal = _detect_candle_type(row, atr, cfg_bt)

        if not candle_type:
            i += 1
            continue

        if signal == "BUY" and not allow_buy:
            i += 1
            continue
        if signal == "SELL" and not allow_sell:
            i += 1
            continue

        # Hitung harga order
        body    = row['body']
        retrace = cfg_bt.retrace_ratio * body
        buf     = cfg_bt.sl_buffer_pips * cfg_bt.pip_size

        if signal == "BUY":
            entry       = row['close'] - retrace
            sl          = row['low']   - buf
            sl_distance = abs(entry - sl)
            tp          = entry + sl_distance * cfg_bt.rr_ratio
        else:
            entry       = row['close'] + retrace
            sl          = row['high']  + buf
            sl_distance = abs(sl - entry)
            tp          = entry - sl_distance * cfg_bt.rr_ratio

        trade = Trade(
            idx         = i,
            time_signal = df.index[i],
            candle_type = candle_type,
            signal      = signal,
            entry       = round(entry, 2),
            sl          = round(sl, 2),
            tp          = round(tp, 2),
            lot         = cfg_bt.lot_size,
        )

        # ── Simulasi pengisian Limit Order di candle[i+1] ─────────────────
        filled = False
        j      = i + 1

        # Cek sampai ORDER_EXPIRY_CANDLES apakah limit order tersentuh
        expiry_end = min(j + cfg_bt.order_expiry_candles, n)
        for k in range(j, expiry_end):
            next_candle = df.iloc[k]
            if signal == "BUY"  and next_candle['low']  <= entry:
                filled = True
                trade.time_entry = df.index[k]
                break
            if signal == "SELL" and next_candle['high'] >= entry:
                filled = True
                trade.time_entry = df.index[k]
                break

        if not filled:
            trade.outcome = "EXPIRED"
            trades.append(trade)
            i += 1
            continue

        # ── Simulasi TP/SL setelah terisi ────────────────────────────────
        outcome_found = False
        for m in range(k, n):
            candle_m = df.iloc[m]

            if signal == "BUY":
                # Cek SL dulu (pesimistis: SL dihit dulu jika range mencakup keduanya)
                if candle_m['low'] <= sl:
                    trade.outcome   = "SL"
                    trade.exit_price = sl
                    trade.time_exit  = df.index[m]
                    trade.pnl_pips   = -sl_distance / cfg_bt.pip_size
                    outcome_found    = True
                    break
                elif candle_m['high'] >= tp:
                    trade.outcome   = "TP"
                    trade.exit_price = tp
                    trade.time_exit  = df.index[m]
                    trade.pnl_pips   = (sl_distance * cfg_bt.rr_ratio) / cfg_bt.pip_size
                    outcome_found    = True
                    break
            else:
                if candle_m['high'] >= sl:
                    trade.outcome   = "SL"
                    trade.exit_price = sl
                    trade.time_exit  = df.index[m]
                    trade.pnl_pips   = -sl_distance / cfg_bt.pip_size
                    outcome_found    = True
                    break
                elif candle_m['low'] <= tp:
                    trade.outcome   = "TP"
                    trade.exit_price = tp
                    trade.time_exit  = df.index[m]
                    trade.pnl_pips   = (sl_distance * cfg_bt.rr_ratio) / cfg_bt.pip_size
                    outcome_found    = True
                    break

        if not outcome_found:
            trade.outcome = "OPEN"

        # PnL dalam USD (1 pip XAUUSD ≈ $1 per 0.10 lot)
        # Untuk lot 0.01: 1 pip = $0.10
        pip_value       = cfg_bt.lot_size / 0.10  # $1 per pip per 0.10 lot
        trade.pnl_usd   = round(trade.pnl_pips * pip_value, 2)

        trades.append(trade)
        i = m + 1 if outcome_found else i + 1

    return trades


# =============================================================================
# BAGIAN 3: KALKULASI STATISTIK & LAPORAN
# =============================================================================

def compute_stats(trades: list[Trade], cfg_bt: BacktestConfig) -> dict:
    """Menghitung semua statistik performa backtest."""
    closed = [t for t in trades if t.outcome in ("TP", "SL")]
    wins   = [t for t in closed if t.outcome == "TP"]
    losses = [t for t in closed if t.outcome == "SL"]
    expired = [t for t in trades if t.outcome == "EXPIRED"]

    total_closed = len(closed)
    win_count    = len(wins)
    loss_count   = len(losses)
    winrate      = (win_count / total_closed * 100) if total_closed > 0 else 0.0

    total_pnl_pips = sum(t.pnl_pips for t in closed)
    total_pnl_usd  = sum(t.pnl_usd  for t in closed)

    avg_win_pips  = np.mean([t.pnl_pips for t in wins])   if wins   else 0
    avg_loss_pips = np.mean([t.pnl_pips for t in losses]) if losses else 0
    avg_rr        = abs(avg_win_pips / avg_loss_pips)      if losses else 0

    # Equity curve
    equity        = cfg_bt.initial_balance
    equity_curve  = [equity]
    max_equity    = equity
    max_drawdown  = 0.0
    for t in closed:
        equity += t.pnl_usd
        equity_curve.append(equity)
        max_equity = max(max_equity, equity)
        drawdown   = (max_equity - equity) / max_equity * 100
        max_drawdown = max(max_drawdown, drawdown)

    final_equity  = equity
    total_return  = (final_equity - cfg_bt.initial_balance) / cfg_bt.initial_balance * 100

    # Per candle type
    candle_stats = {}
    for t in closed:
        ct = t.candle_type
        if ct not in candle_stats:
            candle_stats[ct] = {"total": 0, "win": 0, "pnl_pips": 0}
        candle_stats[ct]["total"]     += 1
        candle_stats[ct]["win"]       += 1 if t.outcome == "TP" else 0
        candle_stats[ct]["pnl_pips"]  += t.pnl_pips

    return {
        "total_signals" : len(trades),
        "total_closed"  : total_closed,
        "win_count"     : win_count,
        "loss_count"    : loss_count,
        "expired_count" : len(expired),
        "winrate"       : winrate,
        "total_pnl_pips": round(total_pnl_pips, 1),
        "total_pnl_usd" : round(total_pnl_usd, 2),
        "avg_win_pips"  : round(avg_win_pips, 1),
        "avg_loss_pips" : round(avg_loss_pips, 1),
        "avg_rr"        : round(avg_rr, 2),
        "max_drawdown"  : round(max_drawdown, 2),
        "total_return"  : round(total_return, 2),
        "final_equity"  : round(final_equity, 2),
        "equity_curve"  : equity_curve,
        "candle_stats"  : candle_stats,
    }


def print_report(stats: dict, cfg_bt: BacktestConfig):
    """Cetak laporan backtest ke terminal."""
    sep = "=" * 60

    print(f"\n{sep}")
    print(f"  LAPORAN BACKTEST — {cfg_bt.symbol} {cfg_bt.timeframe_str}")
    print(f"  Periode  : {cfg_bt.days} hari terakhir")
    print(f"  RR Ratio : 1:{cfg_bt.rr_ratio}")
    print(sep)

    print(f"\n[RINGKASAN TRADE]")
    print(f"  Total Sinyal Terdeteksi : {stats['total_signals']:>6}")
    print(f"  Total Trade Closed      : {stats['total_closed']:>6}")
    print(f"  Order Expired (miss)    : {stats['expired_count']:>6}")
    print(f"  WIN                     : {stats['win_count']:>6}")
    print(f"  LOSS                    : {stats['loss_count']:>6}")

    wr = stats['winrate']
    wr_icon = "[BAGUS]" if wr >= 60 else "[CUKUP]" if wr >= 50 else "[RENDAH]"
    print(f"\n  WIN RATE                : {wr:>5.1f}%  {wr_icon}")
    print(f"  Avg Win  (pips)         : {stats['avg_win_pips']:>7.1f}")
    print(f"  Avg Loss (pips)         : {stats['avg_loss_pips']:>7.1f}")
    print(f"  Actual RR               : 1:{stats['avg_rr']:.2f}")

    print(f"\n[PERFORMA FINANSIAL]")
    print(f"  Balance Awal    : ${cfg_bt.initial_balance:>10,.2f}")
    print(f"  Balance Akhir   : ${stats['final_equity']:>10,.2f}")
    pnl_sign = "+" if stats['total_pnl_usd'] >= 0 else ""
    print(f"  Total PnL       : {pnl_sign}${stats['total_pnl_usd']:>9,.2f}  ({pnl_sign}{stats['total_return']:.2f}%)")
    print(f"  Total Pips      : {stats['total_pnl_pips']:>+.1f} pips")
    print(f"  Max Drawdown    : {stats['max_drawdown']:.2f}%")

    print(f"\n[WIN RATE PER TIPE CANDLE]")
    print(f"  {'Tipe Candle':<22} {'Total':>6} {'Win':>5} {'WR%':>7} {'Pips':>8}")
    print(f"  {'-'*52}")
    for ct, s in sorted(stats['candle_stats'].items(),
                         key=lambda x: x[1]['total'], reverse=True):
        wr_ct = s['win'] / s['total'] * 100 if s['total'] > 0 else 0
        pip_sign = "+" if s['pnl_pips'] >= 0 else ""
        print(f"  {ct:<22} {s['total']:>6} {s['win']:>5} {wr_ct:>6.1f}% {pip_sign}{s['pnl_pips']:>6.1f}")

    print(f"\n{sep}\n")


def save_trades_csv(trades: list[Trade], filename: str = "backtest_trades.csv"):
    """Simpan detail setiap trade ke file CSV."""
    rows = []
    for t in trades:
        rows.append({
            "time_signal" : t.time_signal,
            "candle_type" : t.candle_type,
            "signal"      : t.signal,
            "entry"       : t.entry,
            "sl"          : t.sl,
            "tp"          : t.tp,
            "time_entry"  : t.time_entry,
            "time_exit"   : t.time_exit,
            "exit_price"  : t.exit_price,
            "outcome"     : t.outcome,
            "pnl_pips"    : round(t.pnl_pips, 1),
            "pnl_usd"     : t.pnl_usd,
        })
    df = pd.DataFrame(rows)
    df.to_csv(filename, index=False)
    print(f"[INFO] Detail trade disimpan ke: {filename}")


# =============================================================================
# BAGIAN 4: CLI ENTRY POINT
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Backtest Strategi Momentum Candle M1 XAUUSD"
    )
    parser.add_argument("--days",       type=int,   default=60,   help="Jumlah hari history (default: 60)")
    parser.add_argument("--rr",         type=float, default=cfg.RR_RATIO, help="Risk:Reward ratio")
    parser.add_argument("--body_mult",  type=float, default=cfg.IMPULSE_BODY_MULT, help="Body multiplier ATR")
    parser.add_argument("--retrace",    type=float, default=cfg.RETRACE_RATIO, help="Retrace ratio entry limit (0.38)")
    parser.add_argument("--no_ema",     action="store_true", help="Matikan filter EMA")
    parser.add_argument("--csv",        type=str,   default=None, help="Path file CSV alternatif (jika tanpa MT5)")
    parser.add_argument("--save_csv",   action="store_true", help="Simpan hasil trade ke CSV")
    return parser.parse_args()


def main():
    args = parse_args()

    cfg_bt = BacktestConfig(
        days              = args.days,
        rr_ratio          = args.rr,
        impulse_body_mult = args.body_mult,
        retrace_ratio     = args.retrace,
        use_ema_filter    = not args.no_ema,
    )

    # Ambil data
    if args.csv:
        df = load_csv(args.csv)
    else:
        df = fetch_history_mt5(cfg_bt)

    if df is None or len(df) < 200:
        print("[ERROR] Data tidak cukup untuk backtest (min 200 candle).")
        return

    # Jalankan backtest
    trades = run_backtest(df, cfg_bt)

    if not trades:
        print("[INFO] Tidak ada sinyal ditemukan dalam periode ini.")
        return

    # Hitung statistik
    stats = compute_stats(trades, cfg_bt)

    # Tampilkan laporan
    print_report(stats, cfg_bt)

    # Simpan CSV jika diminta
    if args.save_csv:
        save_trades_csv(trades, f"backtest_{cfg_bt.symbol}_{cfg_bt.days}d.csv")


if __name__ == "__main__":
    main()
