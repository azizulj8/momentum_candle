# =============================================================================
# candle_detector.py — Modul Deteksi 6 Tipe Momentum Candle
# =============================================================================
# Modul ini bertugas menganalisa candle yang sudah close dan menentukan
# apakah candle tersebut termasuk salah satu dari 6 tipe momentum candle.
# Jika ya, modul akan mengembalikan sinyal BUY atau SELL beserta level harga
# untuk memasang Limit Order di candle berikutnya.
# =============================================================================

import pandas as pd
import numpy as np
import logging
from datetime import time as dtime

logger = logging.getLogger(__name__)


# =============================================================================
# BAGIAN 1: KALKULASI INDIKATOR DASAR
# =============================================================================

def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Menghitung Average True Range (ATR).
    ATR adalah ukuran rata-rata volatilitas candle dalam N periode terakhir.
    Semakin besar ATR → pasar sedang bergerak kencang (volatile).
    Semakin kecil ATR → pasar sedang sideways/sepi.
    """
    high  = df['high']
    low   = df['low']
    close = df['close']

    # True Range: nilai terbesar dari 3 kondisi berikut:
    #   1. High - Low (range candle saat ini)
    #   2. |High - Close sebelumnya| (gap naik)
    #   3. |Low  - Close sebelumnya| (gap turun)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # ATR = rata-rata rolling dari True Range selama N periode
    atr = true_range.rolling(window=period).mean()
    return atr


def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Menghitung Exponential Moving Average (EMA).
    EMA lebih responsif terhadap pergerakan harga terbaru dibanding SMA biasa.
    Digunakan sebagai filter tren:
      - Harga di ATAS EMA → tren naik  → hanya cari sinyal BUY
      - Harga di BAWAH EMA → tren turun → hanya cari sinyal SELL
    """
    return df['close'].ewm(span=period, adjust=False).mean()


def add_candle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Menambahkan kolom-kolom dasar ke DataFrame untuk analisa candle:
      - body      : ukuran body candle (jarak Open ke Close)
      - upper_wick: panjang sumbu atas
      - lower_wick: panjang sumbu bawah
      - total_range: jarak High ke Low (keseluruhan panjang candle)
      - is_bullish : True jika candle hijau (Close > Open)
    """
    df = df.copy()

    df['body']        = (df['close'] - df['open']).abs()
    df['upper_wick']  = df['high'] - df[['open', 'close']].max(axis=1)
    df['lower_wick']  = df[['open', 'close']].min(axis=1) - df['low']
    df['total_range'] = df['high'] - df['low']
    df['is_bullish']  = df['close'] > df['open']

    # Hindari pembagian dengan nol pada candle doji (total_range = 0)
    df['total_range'] = df['total_range'].replace(0, np.nan)

    # Rasio (persentase) masing-masing bagian terhadap total range
    df['body_ratio']        = df['body']        / df['total_range']
    df['upper_wick_ratio']  = df['upper_wick']  / df['total_range']
    df['lower_wick_ratio']  = df['lower_wick']  / df['total_range']

    return df


# =============================================================================
# BAGIAN 1B: FILTER KUALITAS SINYAL
# =============================================================================

def is_london_ny_session(ts: pd.Timestamp) -> bool:
    """
    Filter Sesi Trading: hanya London (07:00-12:00 UTC) dan New York (12:00-20:00 UTC).
    Asian session (20:00-07:00 UTC) dihindari karena volume rendah dan banyak noise.
    XAUUSD paling volatile dan akurat sinyalnya di sesi London dan NY overlap.
    """
    t = ts.time()
    return dtime(7, 0) <= t <= dtime(20, 0)


def has_volume_spike(df: pd.DataFrame, idx: int, lookback: int = 20,
                     multiplier: float = 1.3) -> bool:
    """
    Filter Volume: pastikan candle sinyal punya tick_volume lebih tinggi
    dari rata-rata N candle sebelumnya.
    Volume tinggi = ada partisipasi pasar yang kuat → sinyal lebih valid.
    """
    if 'tick_volume' not in df.columns:
        return True  # skip filter jika tidak ada data volume
    if idx < lookback:
        return False
    avg_vol     = df['tick_volume'].iloc[idx - lookback: idx].mean()
    candle_vol  = df['tick_volume'].iloc[idx]
    return candle_vol >= avg_vol * multiplier


def has_trend_momentum(df: pd.DataFrame, idx: int, signal: str,
                        lookback: int = 3) -> bool:
    """
    Filter Trend Momentum: pastikan N candle sebelum sinyal sudah bergerak
    searah dengan sinyal. Ini memastikan kita mengikuti tren, bukan melawan.

    BUY : minimal (lookback-1) dari N candle terakhir harus bullish (Close > Open)
    SELL: minimal (lookback-1) dari N candle terakhir harus bearish (Close < Open)

    Contoh lookback=3: minimal 2 dari 3 candle sebelumnya harus searah sinyal.
    """
    if idx < lookback:
        return False
    prev_candles = df.iloc[idx - lookback: idx]
    bullish_count = (prev_candles['close'] > prev_candles['open']).sum()
    bearish_count = lookback - bullish_count
    min_required  = lookback - 1  # minimal N-1 candle searah

    if signal == "BUY":
        return bullish_count >= min_required
    else:
        return bearish_count >= min_required


def has_clean_structure(df: pd.DataFrame, idx: int, signal: str,
                         atr: float, lookback: int = 5) -> bool:
    """
    Filter Struktur Harga: pastikan tidak ada candle reversal besar
    dalam N candle sebelumnya yang berlawanan dengan sinyal.
    Menghindari entry di tengah-tengah konsolidasi atau setelah reversal.

    Jika ada candle dalam lookback yang bodynya > 1.5x ATR dan arahnya
    berlawanan dengan sinyal → SKIP (struktur tidak bersih).
    """
    if pd.isna(atr) or atr == 0 or idx < lookback:
        return True
    prev = df.iloc[idx - lookback: idx]
    for _, row in prev.iterrows():
        body = abs(row['close'] - row['open'])
        if body < 1.5 * atr:
            continue
        is_bull = row['close'] > row['open']
        if signal == "BUY"  and not is_bull:
            return False  # ada candle bearish besar → struktur tidak bersih
        if signal == "SELL" and is_bull:
            return False  # ada candle bullish besar → struktur tidak bersih
    return True


# =============================================================================
# BAGIAN 2: FUNGSI DETEKSI 6 TIPE CANDLE
# =============================================================================

def detect_marubozu_bullish(row: pd.Series, atr_value: float, cfg) -> bool:
    """
    TIPE 1: Marubozu Bullish (Sinyal BUY Terkuat)
    ──────────────────────────────────────────────
    Ciri-ciri:
      ✅ Candle hijau (Close > Open)
      ✅ Body sangat besar: >= MARUBOZU_BODY_MULT × ATR
      ✅ Upper wick sangat kecil: <= MARUBOZU_MAX_WICK (5%) dari total range
      ✅ Lower wick sangat kecil: <= MARUBOZU_MAX_WICK (5%) dari total range
    Artinya: Buyer mendominasi penuh dari awal candle hingga close, tanpa ada
    perlawanan dari seller sama sekali. Sinyal momentum paling kuat.
    """
    if pd.isna(atr_value) or atr_value == 0:
        return False

    return (
        row['is_bullish'] and
        row['body']       >= cfg.MARUBOZU_BODY_MULT * atr_value and
        row['upper_wick_ratio'] <= cfg.MARUBOZU_MAX_WICK and
        row['lower_wick_ratio'] <= cfg.MARUBOZU_MAX_WICK
    )


def detect_bullish_impulse(row: pd.Series, atr_value: float, cfg) -> bool:
    """
    TIPE 2: Bullish Impulse (Sinyal BUY Kuat)
    ──────────────────────────────────────────
    Ciri-ciri:
      ✅ Candle hijau (Close > Open)
      ✅ Body besar: >= IMPULSE_BODY_MULT × ATR
      ✅ Upper wick kecil: <= IMPULSE_MAX_WICK (20%) dari total range
      ✅ Lower wick kecil: <= IMPULSE_MAX_WICK (20%) dari total range
    Artinya: Ada momentum beli yang kuat. Wick sedikit ada (toleransi lebih
    longgar dari Marubozu) tapi buyer tetap jelas mendominasi di penutupan.
    """
    if pd.isna(atr_value) or atr_value == 0:
        return False

    # Pastikan tidak dobel-terdeteksi sebagai Marubozu
    is_marubozu = detect_marubozu_bullish(row, atr_value, cfg)

    return (
        not is_marubozu and
        row['is_bullish'] and
        row['body']       >= cfg.IMPULSE_BODY_MULT * atr_value and
        row['upper_wick_ratio'] <= cfg.IMPULSE_MAX_WICK and
        row['lower_wick_ratio'] <= cfg.IMPULSE_MAX_WICK
    )


def detect_hammer(row: pd.Series, cfg) -> bool:
    """
    TIPE 3: Hammer / Bullish Pin Bar (Sinyal BUY Reversal)
    ────────────────────────────────────────────────────────
    Ciri-ciri:
      ✅ Lower wick sangat panjang: >= PINBAR_MIN_WICK (60%) dari total range
      ✅ Upper wick sangat pendek:  <= PINBAR_MAX_OPP_WICK (10%) dari total range
      ✅ Body kecil, berada di bagian atas candle
    Artinya: Seller sempat mendorong harga sangat jauh ke bawah (lower wick
    panjang), tetapi buyer berhasil MENOLAK dan menutup harga di area atas.
    Ini menunjukkan buyer kuat. Paling efektif di area support / demand zone.
    """
    if pd.isna(row.get('total_range')) or row['total_range'] == 0:
        return False

    return (
        row['lower_wick_ratio'] >= cfg.PINBAR_MIN_WICK and
        row['upper_wick_ratio'] <= cfg.PINBAR_MAX_OPP_WICK
    )


def detect_marubozu_bearish(row: pd.Series, atr_value: float, cfg) -> bool:
    """
    TIPE 4: Marubozu Bearish (Sinyal SELL Terkuat)
    ───────────────────────────────────────────────
    Ciri-ciri:
      ✅ Candle merah (Close < Open)
      ✅ Body sangat besar: >= MARUBOZU_BODY_MULT × ATR
      ✅ Upper wick sangat kecil: <= MARUBOZU_MAX_WICK (5%) dari total range
      ✅ Lower wick sangat kecil: <= MARUBOZU_MAX_WICK (5%) dari total range
    Artinya: Seller mendominasi penuh dari awal candle hingga close tanpa ada
    perlawanan dari buyer. Sinyal momentum jual paling kuat.
    """
    if pd.isna(atr_value) or atr_value == 0:
        return False

    return (
        not row['is_bullish'] and
        row['body']       >= cfg.MARUBOZU_BODY_MULT * atr_value and
        row['upper_wick_ratio'] <= cfg.MARUBOZU_MAX_WICK and
        row['lower_wick_ratio'] <= cfg.MARUBOZU_MAX_WICK
    )


def detect_bearish_impulse(row: pd.Series, atr_value: float, cfg) -> bool:
    """
    TIPE 5: Bearish Impulse (Sinyal SELL Kuat)
    ───────────────────────────────────────────
    Ciri-ciri:
      ✅ Candle merah (Close < Open)
      ✅ Body besar: >= IMPULSE_BODY_MULT × ATR
      ✅ Wick-wick kecil: <= IMPULSE_MAX_WICK (20%) dari total range
    Artinya: Momentum jual kuat. Ada toleransi wick sedikit tapi seller
    jelas lebih dominan di penutupan candle.
    """
    if pd.isna(atr_value) or atr_value == 0:
        return False

    is_marubozu = detect_marubozu_bearish(row, atr_value, cfg)

    return (
        not is_marubozu and
        not row['is_bullish'] and
        row['body']       >= cfg.IMPULSE_BODY_MULT * atr_value and
        row['upper_wick_ratio'] <= cfg.IMPULSE_MAX_WICK and
        row['lower_wick_ratio'] <= cfg.IMPULSE_MAX_WICK
    )


def detect_shooting_star(row: pd.Series, cfg) -> bool:
    """
    TIPE 6: Shooting Star / Bearish Pin Bar (Sinyal SELL Reversal)
    ───────────────────────────────────────────────────────────────
    Ciri-ciri:
      ✅ Upper wick sangat panjang: >= PINBAR_MIN_WICK (60%) dari total range
      ✅ Lower wick sangat pendek:  <= PINBAR_MAX_OPP_WICK (10%) dari total range
      ✅ Body kecil, berada di bagian bawah candle
    Artinya: Buyer sempat mendorong harga sangat jauh ke atas (upper wick
    panjang), tetapi seller berhasil MENOLAK dan menutup harga di area bawah.
    Ini menunjukkan seller kuat. Paling efektif di area resistance / supply zone.
    """
    if pd.isna(row.get('total_range')) or row['total_range'] == 0:
        return False

    return (
        row['upper_wick_ratio'] >= cfg.PINBAR_MIN_WICK and
        row['lower_wick_ratio'] <= cfg.PINBAR_MAX_OPP_WICK
    )


# =============================================================================
# BAGIAN 3: KALKULASI HARGA LIMIT ORDER
# =============================================================================

def calculate_limit_order_prices(row: pd.Series, signal: str, cfg, pip_size: float) -> dict:
    """
    Menghitung harga-harga untuk Limit Order setelah candle sinyal close.

    Logika:
    ─────────────────────────────────────────────────────────────────────
    BUY LIMIT:
      - Entry   = Close - (RETRACE_RATIO × Body)   ← tunggu pullback kecil
      - SL      = Low candle sinyal - SL_BUFFER     ← jika harga tembus Low, sinyal gagal
      - TP      = Entry + (SL distance × RR_RATIO)  ← target keuntungan

    SELL LIMIT:
      - Entry   = Close + (RETRACE_RATIO × Body)   ← tunggu retest kecil ke atas
      - SL      = High candle sinyal + SL_BUFFER    ← jika harga tembus High, sinyal gagal
      - TP      = Entry - (SL distance × RR_RATIO)  ← target keuntungan
    ─────────────────────────────────────────────────────────────────────

    Returns:
        dict berisi: entry, sl, tp, sl_distance, signal
    """
    sl_buffer = cfg.SL_BUFFER_PIPS * pip_size
    body      = row['body']
    retrace   = cfg.RETRACE_RATIO * body

    if signal == "BUY":
        entry       = round(row['close'] - retrace, 5)
        sl          = round(row['low']   - sl_buffer, 5)
        sl_distance = abs(entry - sl)
        tp          = round(entry + sl_distance * cfg.RR_RATIO, 5)

    elif signal == "SELL":
        entry       = round(row['close'] + retrace, 5)
        sl          = round(row['high']  + sl_buffer, 5)
        sl_distance = abs(sl - entry)
        tp          = round(entry - sl_distance * cfg.RR_RATIO, 5)

    else:
        return {}

    return {
        "signal"     : signal,
        "entry"      : entry,
        "sl"         : sl,
        "tp"         : tp,
        "sl_distance": round(sl_distance, 5),
        "rr_ratio"   : cfg.RR_RATIO,
    }


# =============================================================================
# BAGIAN 4: FUNGSI UTAMA — ANALISA CANDLE
# =============================================================================

def analyze_candle(df: pd.DataFrame, cfg) -> dict | None:
    """
    Fungsi utama yang menganalisa candle TERAKHIR yang sudah close.

    Filter v2 (Win Rate 70%+):
    1. Hitung metrik candle (body, wick, range)
    2. Hitung ATR14 dan EMA20
    3. Filter Sesi: hanya London (07-12 UTC) dan NY (12-20 UTC)
    4. Filter EMA: hanya BUY di atas EMA, SELL di bawah EMA
    5. Deteksi tipe candle: HANYA Bullish/Bearish Impulse
       (Marubozu & Pin Bar dinonaktifkan — WR-nya terlalu rendah)
    6. Filter Volume: candle sinyal harus punya volume di atas rata-rata
    7. Filter Trend Momentum: minimal 2 dari 3 candle sebelumnya searah
    8. Filter Struktur: tidak ada candle besar berlawanan dalam 5 candle terakhir
    9. Hitung harga Limit Order
    """
    if len(df) < cfg.CANDLES_NEEDED:
        logger.warning(f"Data tidak cukup: {len(df)} candle (butuh {cfg.CANDLES_NEEDED})")
        return None

    df.columns = [c.lower() for c in df.columns]
    df = add_candle_metrics(df)
    df['atr'] = calculate_atr(df, cfg.ATR_PERIOD)
    df['ema'] = calculate_ema(df, cfg.EMA_PERIOD)

    # Candle -2 = baru saja close; candle -1 = sedang berjalan (skip)
    idx    = len(df) - 2
    candle = df.iloc[idx]
    atr    = df['atr'].iloc[idx]
    ema    = df['ema'].iloc[idx]
    price  = df['close'].iloc[-1]
    ts     = df.index[idx]

    if pd.isna(atr) or atr == 0:
        return None

    # ── Filter 1: Sesi London/NY ──────────────────────────────────────────────
    use_session = getattr(cfg, 'USE_SESSION_FILTER', True)
    if use_session and not is_london_ny_session(ts):
        logger.debug(f"[SKIP] Di luar sesi trading: {ts.time()}")
        return None

    # ── Filter 2: Arah tren via EMA ───────────────────────────────────────────
    if cfg.USE_EMA_FILTER:
        trend_up   = price > ema
        trend_down = price < ema
    else:
        trend_up = trend_down = True

    pip_size    = 0.10
    candle_type = None
    signal      = None
    use_market  = False  # False = Limit Order, True = Market Order langsung

    # ── Filter 3: Deteksi candle ──────────────────────────────────────────────
    #
    # MARUBOZU  → momentum PENUH, tidak ada wick sama sekali
    #             Strategi: MARKET ORDER langsung (jangan tunggu pullback!)
    #             Alasan: Setelah Marubozu, pullback sering sangat dalam
    #             sehingga Limit Order sering kena SL sebelum lanjut.
    #             Masuk langsung di market lebih aman untuk pola ini.
    #
    # IMPULSE   → momentum kuat, ada sedikit wick
    #             Strategi: LIMIT ORDER (tunggu pullback 38%)
    #             Alasan: Setelah Impulse, harga sering pullback wajar
    #             sebelum lanjut searah sinyal.
    #
    if trend_up:
        if detect_marubozu_bullish(candle, atr, cfg):
            candle_type = "Marubozu Bullish"
            signal      = "BUY"
            use_market  = True   # Marubozu: langsung market order
        elif detect_bullish_impulse(candle, atr, cfg):
            candle_type = "Bullish Impulse"
            signal      = "BUY"
            use_market  = False  # Impulse: tunggu pullback via limit order

    if trend_down and signal is None:
        if detect_marubozu_bearish(candle, atr, cfg):
            candle_type = "Marubozu Bearish"
            signal      = "SELL"
            use_market  = True   # Marubozu: langsung market order
        elif detect_bearish_impulse(candle, atr, cfg):
            candle_type = "Bearish Impulse"
            signal      = "SELL"
            use_market  = False  # Impulse: tunggu pullback via limit order

    if signal is None:
        return None

    # ── Filter 4: Volume spike ────────────────────────────────────────────────
    use_vol = getattr(cfg, 'USE_VOLUME_FILTER', True)
    vol_mult = getattr(cfg, 'VOLUME_MULTIPLIER', 1.3)
    if use_vol and not has_volume_spike(df, idx, multiplier=vol_mult):
        logger.debug(f"[SKIP] Volume tidak cukup kuat pada {ts}")
        return None

    # ── Filter 5: Trend momentum (3 candle sebelumnya searah) ─────────────────
    use_mom = getattr(cfg, 'USE_TREND_MOMENTUM', True)
    mom_lb  = getattr(cfg, 'TREND_MOMENTUM_LOOKBACK', 3)
    if use_mom and not has_trend_momentum(df, idx, signal, mom_lb):
        logger.debug(f"[SKIP] Trend momentum tidak cukup pada {ts}")
        return None

    # ── Filter 6: Struktur harga bersih ───────────────────────────────────────
    use_struct = getattr(cfg, 'USE_STRUCTURE_FILTER', True)
    if use_struct and not has_clean_structure(df, idx, signal, atr):
        logger.debug(f"[SKIP] Struktur harga tidak bersih pada {ts}")
        return None

    # ── Hitung harga order ────────────────────────────────────────────────────
    order_prices = calculate_limit_order_prices(candle, signal, cfg, pip_size)

    result = {
        "candle_type"    : candle_type,
        "signal"         : signal,
        "use_market_order": use_market,   # True=market order, False=limit order
        "candle_open"    : candle['open'],
        "candle_high"    : candle['high'],
        "candle_low"     : candle['low'],
        "candle_close"   : candle['close'],
        "body_size"      : round(candle['body'], 4),
        "atr"            : round(atr, 4),
        "ema"            : round(ema, 2),
        **order_prices,
    }

    order_mode = "MARKET" if use_market else "LIMIT"
    logger.info(
        f"[SIGNAL] {candle_type} | {signal} | Mode: {order_mode} | "
        f"Entry: {order_prices['entry']} | SL: {order_prices['sl']} | "
        f"TP: {order_prices['tp']}"
    )
    return result
