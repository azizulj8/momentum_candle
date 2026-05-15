# =============================================================================
# bot.py — Loop Utama Robot Scalping Momentum M1 (XAUUSD)
# =============================================================================
# Cara kerja:
#   1. Konek ke MetaTrader 5
#   2. Setiap candle M1 close → ambil data terbaru
#   3. Jalankan analisa candle (candle_detector.py)
#   4. Jika ada sinyal → pasang Limit Order (Pending Order) di candle berikutnya
#   5. Monitor order: jika tidak tersentuh dalam 60 detik → otomatis cancel
#   6. Kirim notifikasi Telegram untuk setiap event penting
# =============================================================================

import sys
import io
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import config as cfg
from candle_detector import analyze_candle
from order_executor import (
    execute_signal,
    cancel_all_pending,
    get_open_positions,
    get_pending_orders,
    get_account_info,
)

# =============================================================================
# SETUP LOGGING
# Fix: Windows cmd menggunakan CP1252 yang tidak bisa encode emoji.
# Solusi: paksa StreamHandler menggunakan UTF-8 secara eksplisit.
# =============================================================================
def _make_stream_handler() -> logging.StreamHandler:
    """Membuat StreamHandler dengan encoding UTF-8 agar aman di Windows."""
    try:
        # Python 3.9+: buka stdout buffer dengan encoding UTF-8
        utf8_stream = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding='utf-8',
            errors='replace',  # karakter yang tidak bisa di-encode → diganti '?'
            line_buffering=True,
        )
        handler = logging.StreamHandler(utf8_stream)
    except AttributeError:
        # Fallback: sys.stdout tidak punya .buffer (misal saat di-redirect)
        handler = logging.StreamHandler(sys.stdout)
    return handler

log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log_level     = getattr(logging, cfg.LOG_LEVEL, logging.INFO)

_stream_handler = _make_stream_handler()
_stream_handler.setFormatter(log_formatter)
_stream_handler.setLevel(log_level)

_file_handler = logging.FileHandler(cfg.LOG_FILE, mode='a', encoding='utf-8')
_file_handler.setFormatter(log_formatter)
_file_handler.setLevel(log_level)

logging.basicConfig(level=log_level, handlers=[_stream_handler, _file_handler])
logger = logging.getLogger("MomentumBot")


# =============================================================================
# BAGIAN 1: KONEKSI METATRADER 5
# =============================================================================

def connect_mt5() -> bool:
    """
    Inisialisasi dan login ke MetaTrader 5.
    Returns True jika berhasil, False jika gagal.
    """
    if mt5 is None:
        logger.error("Library MetaTrader5 tidak ditemukan. Install dengan: pip install MetaTrader5")
        return False

    # Inisialisasi terminal MT5
    if not mt5.initialize():
        logger.error(f"MT5 initialize() gagal: {mt5.last_error()}")
        return False

    # Login ke akun broker
    authorized = mt5.login(
        login   = cfg.MT5_ACCOUNT,
        password= cfg.MT5_PASSWORD,
        server  = cfg.MT5_SERVER,
    )

    if not authorized:
        logger.error(f"Login MT5 gagal: {mt5.last_error()}")
        mt5.shutdown()
        return False

    account_info = mt5.account_info()
    logger.info(
        f"[OK] Terkoneksi ke MT5 | Akun: {account_info.login} | "
        f"Balance: {account_info.balance:.2f} {account_info.currency} | "
        f"Server: {account_info.server}"
    )
    send_telegram(f"Robot Momentum M1 aktif\nAkun: {account_info.login}\nBalance: {account_info.balance:.2f} {account_info.currency}")
    return True


def disconnect_mt5():
    """Menutup koneksi ke MT5 dengan bersih."""
    if mt5:
        mt5.shutdown()
        logger.info("MT5 terputus.")


# =============================================================================
# BAGIAN 2: PENGAMBILAN DATA CANDLE (OHLCV)
# =============================================================================

# Mapping string timeframe ke konstanta MT5
TIMEFRAME_MAP = {
    "M1" : mt5.TIMEFRAME_M1  if mt5 else 1,
    "M5" : mt5.TIMEFRAME_M5  if mt5 else 5,
    "M15": mt5.TIMEFRAME_M15 if mt5 else 15,
    "H1" : mt5.TIMEFRAME_H1  if mt5 else 60,
}

def get_candles(symbol: str, timeframe_str: str, count: int) -> pd.DataFrame | None:
    """
    Mengambil data candle OHLCV dari MT5 dan mengembalikannya sebagai DataFrame.

    Args:
        symbol        : Nama simbol, misal "XAUUSDm"
        timeframe_str : String timeframe, misal "M1"
        count         : Jumlah candle yang diambil (termasuk candle live)

    Returns:
        DataFrame dengan kolom [time, open, high, low, close, tick_volume]
        atau None jika terjadi error.
    """
    if mt5 is None:
        logger.error("MT5 tidak tersedia.")
        return None

    tf = TIMEFRAME_MAP.get(timeframe_str)
    if tf is None:
        logger.error(f"Timeframe tidak dikenal: {timeframe_str}")
        return None

    # copy_rates_from_pos(symbol, tf, start_pos, count)
    # start_pos=0 → mulai dari candle paling baru
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)

    if rates is None or len(rates) == 0:
        logger.error(f"Gagal mengambil data candle {symbol}: {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.columns = [c.lower() for c in df.columns]

    # Candle terakhir (-1) masih dalam proses (belum close), candle -2 sudah close
    logger.debug(
        f"Data berhasil diambil: {len(df)} candle | "
        f"Candle terakhir close: {df.index[-2]} | "
        f"Candle live: {df.index[-1]}"
    )
    return df


# =============================================================================
# BAGIAN 3: MANAJEMEN ORDER — didelegasikan ke order_executor.py
# =============================================================================
# Semua logika eksekusi order (BUY/SELL limit & market, cancel, close)
# ada di order_executor.py agar kode lebih terstruktur dan mudah di-maintain.


# =============================================================================
# BAGIAN 4: NOTIFIKASI TELEGRAM
# =============================================================================

def send_telegram(message: str):
    """Mengirim pesan notifikasi ke Telegram."""
    if not cfg.TELEGRAM_ENABLED:
        return
    try:
        url  = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id"   : cfg.TELEGRAM_CHAT_ID,
            "text"      : message,
            "parse_mode": "Markdown",
        }
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram gagal: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram error: {e}")


# =============================================================================
# BAGIAN 5: LOOP UTAMA ROBOT
# =============================================================================

def wait_for_candle_close() -> datetime:
    """
    Menunggu hingga candle M1 berikutnya close.
    Cara kerja: tidur sampai detik ke-0 dari menit berikutnya.
    Ini memastikan robot hanya menganalisa candle yang sudah benar-benar close.
    """
    now     = datetime.now(timezone.utc)
    seconds = now.second + now.microsecond / 1_000_000
    sleep_s = 60 - seconds + 0.5  # +0.5 detik toleransi agar candle benar-benar close

    logger.debug(f"Menunggu candle close dalam {sleep_s:.1f} detik...")
    time.sleep(sleep_s)

    return datetime.now(timezone.utc)


def run():
    """
    Loop utama robot. Berjalan terus menerus sampai dihentikan secara manual.

    Alur setiap siklus:
    ┌──────────────────────────────────────────────────────────┐
    │  [Tunggu candle M1 close]                                │
    │        ↓                                                 │
    │  [Ambil 100 candle terbaru dari MT5]                     │
    │        ↓                                                 │
    │  [Analisa candle (candle_detector.py)]                   │
    │        ↓                                                 │
    │  Ada sinyal?                                             │
    │    ├─ YA  → Cek posisi terbuka → Pasang Limit Order     │
    │    └─ TIDAK → Tunggu candle berikutnya                   │
    │        ↓                                                 │
    │  [Cancel order yang expired]                             │
    │        ↓                                                 │
    │  [Ulangi]                                                │
    └──────────────────────────────────────────────────────────┘
    """
    logger.info("=" * 60)
    logger.info("  [START] Robot Scalping Momentum M1 -- XAUUSD  ")
    logger.info("=" * 60)

    if not connect_mt5():
        logger.critical("Gagal konek ke MT5. Robot berhenti.")
        return

    symbol = cfg.SYMBOL

    try:
        while True:
            # ── Tunggu candle berikutnya close ───────────────────────────────
            candle_close_time = wait_for_candle_close()
            logger.info(f"--- Candle close: {candle_close_time.strftime('%Y-%m-%d %H:%M:%S')} UTC ---")

            # ── Ambil data candle terbaru ─────────────────────────────────────
            df = get_candles(symbol, cfg.TIMEFRAME, cfg.CANDLES_NEEDED + 2)
            if df is None:
                logger.warning("Data candle tidak tersedia. Skip siklus ini.")
                continue

            # ── Bersihkan pending order kadaluarsa (fallback safety) ──────────
            cancel_all_pending(symbol)

            # ── Cek posisi & pending order ────────────────────────────────────
            open_pos = get_open_positions(symbol)
            pending  = get_pending_orders(symbol)

            if len(open_pos) >= cfg.MAX_OPEN_POSITIONS:
                logger.info(f"[WAIT] Posisi terbuka: {len(open_pos)}. Menunggu posisi selesai.")
                continue

            if len(pending) > 0:
                logger.info(f"[WAIT] Masih ada {len(pending)} pending order. Skip analisa.")
                continue

            # ── Analisa candle ────────────────────────────────────────────────
            signal_data = analyze_candle(df, cfg)

            if signal_data is None:
                logger.info("[--] Tidak ada momentum candle terdeteksi. Standby...")
                continue

            # ── Ada sinyal → eksekusi via order_executor ─────────────────────
            logger.info(
                f"[SIGNAL] {signal_data['signal']} | "
                f"Tipe: {signal_data['candle_type']} | "
                f"Entry: {signal_data['entry']} | "
                f"SL: {signal_data['sl']} | TP: {signal_data['tp']}"
            )

            # Marubozu → Market Order langsung | Impulse → Limit Order (tunggu pullback)
            use_market = signal_data.get('use_market_order', False)
            result = execute_signal(signal_data, symbol, use_market_order=use_market)
            if result['success']:
                mode = "MARKET" if use_market else "LIMIT"
                logger.info(f"[OK] {mode} order berhasil | Ticket: {result['ticket']} | Tipe: {result['order_type']}")
            else:
                logger.error(f"[FAIL] Order gagal | {result['error']}")

    except KeyboardInterrupt:
        logger.info("[STOP] Robot dihentikan oleh user (Ctrl+C).")
        send_telegram("[STOP] Robot Momentum M1 dihentikan secara manual.")

    except Exception as e:
        logger.exception(f"[ERROR] Error tidak terduga: {e}")
        send_telegram(f"[ERROR] Robot ERROR: {e}")

    finally:
        disconnect_mt5()
        logger.info("Robot selesai.")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    run()
