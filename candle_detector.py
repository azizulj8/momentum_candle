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

    Alur kerja:
    1. Hitung metrik candle (body, wick, range)
    2. Hitung ATR14 dan EMA20
    3. Ambil data candle terakhir (candle yang baru saja close)
    4. Periksa apakah termasuk salah satu dari 6 tipe momentum candle
    5. Jika ya → hitung harga Limit Order (Entry, SL, TP)
    6. Kembalikan hasil analisa sebagai dict, atau None jika tidak ada sinyal

    Args:
        df  : DataFrame dengan kolom [open, high, low, close, tick_volume]
              minimal CANDLES_NEEDED baris, diurutkan dari candle terlama
        cfg : objek config (dari config.py)

    Returns:
        dict berisi sinyal dan level harga, atau None jika tidak ada sinyal
    """
    if len(df) < cfg.CANDLES_NEEDED:
        logger.warning(f"Data tidak cukup: {len(df)} candle (butuh {cfg.CANDLES_NEEDED})")
        return None

    # Pastikan nama kolom lowercase
    df.columns = [c.lower() for c in df.columns]

    # ── Step 1: Tambahkan metrik candle ──────────────────────────────────────
    df = add_candle_metrics(df)

    # ── Step 2: Hitung ATR dan EMA ───────────────────────────────────────────
    df['atr'] = calculate_atr(df, cfg.ATR_PERIOD)
    df['ema'] = calculate_ema(df, cfg.EMA_PERIOD)

    # ── Step 3: Ambil candle terakhir yang sudah close ───────────────────────
    # Index -1 = candle yang sedang berjalan (BELUM close, skip!)
    # Index -2 = candle yang baru saja close → ini yang dianalisa
    candle = df.iloc[-2]   # Candle yang baru close
    atr    = df['atr'].iloc[-2]
    ema    = df['ema'].iloc[-2]
    price  = df['close'].iloc[-1]  # Harga live saat ini (untuk cek posisi vs EMA)

    logger.debug(
        f"Candle Close: O={candle['open']:.2f} H={candle['high']:.2f} "
        f"L={candle['low']:.2f} C={candle['close']:.2f} | "
        f"Body={candle['body']:.4f} ATR={atr:.4f} EMA={ema:.2f}"
    )

    # ── Step 4: Filter tren via EMA ──────────────────────────────────────────
    # Tentukan arah tren berdasarkan posisi harga live vs EMA
    if cfg.USE_EMA_FILTER:
        trend_up   = price > ema   # Tren naik: hanya cari BUY
        trend_down = price < ema   # Tren turun: hanya cari SELL
    else:
        trend_up   = True          # Tanpa filter: semua arah diizinkan
        trend_down = True

    # ── Step 5: Deteksi tipe candle ──────────────────────────────────────────
    candle_type = None
    signal      = None

    # Pip size untuk XAUUSD (1 pip = 0.10 untuk Gold di MT5)
    pip_size = 0.10

    # ── BUY signals ──
    if trend_up:
        if detect_marubozu_bullish(candle, atr, cfg):
            candle_type = "Marubozu Bullish"
            signal      = "BUY"
        elif detect_bullish_impulse(candle, atr, cfg):
            candle_type = "Bullish Impulse"
            signal      = "BUY"
        elif detect_hammer(candle, cfg):
            candle_type = "Hammer"
            signal      = "BUY"

    # ── SELL signals ──
    if trend_down and signal is None:
        if detect_marubozu_bearish(candle, atr, cfg):
            candle_type = "Marubozu Bearish"
            signal      = "SELL"
        elif detect_bearish_impulse(candle, atr, cfg):
            candle_type = "Bearish Impulse"
            signal      = "SELL"
        elif detect_shooting_star(candle, cfg):
            candle_type = "Shooting Star"
            signal      = "SELL"

    # ── Step 6: Tidak ada sinyal → return None ────────────────────────────────
    if signal is None:
        logger.debug("Tidak ada sinyal momentum terdeteksi pada candle ini.")
        return None

    # ── Step 7: Hitung harga Limit Order ─────────────────────────────────────
    order_prices = calculate_limit_order_prices(candle, signal, cfg, pip_size)

    result = {
        "candle_type"  : candle_type,
        "signal"       : signal,
        "candle_open"  : candle['open'],
        "candle_high"  : candle['high'],
        "candle_low"   : candle['low'],
        "candle_close" : candle['close'],
        "body_size"    : round(candle['body'], 4),
        "atr"          : round(atr, 4),
        "ema"          : round(ema, 2),
        **order_prices,
    }

    logger.info(
        f"✅ SINYAL TERDETEKSI | {candle_type} | {signal} | "
        f"Entry: {order_prices['entry']} | SL: {order_prices['sl']} | "
        f"TP: {order_prices['tp']}"
    )

    return result
