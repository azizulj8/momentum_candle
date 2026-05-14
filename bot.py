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

# =============================================================================
# SETUP LOGGING
# =============================================================================
logging.basicConfig(
    level   = getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),                      # tampil di terminal
        logging.FileHandler(cfg.LOG_FILE, mode='a'),  # simpan ke file log
    ]
)
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
        f"✅ Terkoneksi ke MT5 | Akun: {account_info.login} | "
        f"Balance: {account_info.balance:.2f} {account_info.currency} | "
        f"Server: {account_info.server}"
    )
    send_telegram(f"🤖 Robot Momentum M1 aktif\nAkun: {account_info.login}\nBalance: {account_info.balance:.2f} {account_info.currency}")
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
# BAGIAN 3: MEMASANG DAN MENGELOLA LIMIT ORDER
# =============================================================================

def count_open_positions(symbol: str) -> int:
    """Menghitung jumlah posisi yang sedang terbuka untuk simbol tertentu."""
    if mt5 is None:
        return 0
    positions = mt5.positions_get(symbol=symbol)
    return len(positions) if positions else 0


def count_pending_orders(symbol: str) -> int:
    """Menghitung jumlah pending order yang belum terisi untuk simbol tertentu."""
    if mt5 is None:
        return 0
    orders = mt5.orders_get(symbol=symbol)
    return len(orders) if orders else 0


def get_symbol_info(symbol: str) -> dict | None:
    """Mengambil informasi symbol: lot min, pip size, dll."""
    if mt5 is None:
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info tidak ditemukan: {symbol}")
        return None
    return info


def calculate_lot_size(symbol: str, sl_distance: float) -> float:
    """
    Menghitung ukuran lot berdasarkan risiko.
    Jika USE_FIXED_LOT=True → gunakan FIXED_LOT dari config.
    Jika USE_FIXED_LOT=False → hitung lot berdasarkan RISK_PERCENT dari balance.
    """
    if cfg.USE_FIXED_LOT:
        return cfg.FIXED_LOT

    # Kalkulasi lot otomatis berdasarkan risiko
    if mt5 is None or sl_distance == 0:
        return cfg.FIXED_LOT

    account    = mt5.account_info()
    balance    = account.balance
    risk_money = balance * cfg.RISK_PERCENT  # uang yang dirisikkan per trade

    sym_info   = get_symbol_info(symbol)
    if sym_info is None:
        return cfg.FIXED_LOT

    # Nilai per pip per lot (untuk XAUUSD biasanya $1 per pip per 0.01 lot)
    tick_value = sym_info.trade_tick_value
    tick_size  = sym_info.trade_tick_size

    if tick_size == 0:
        return cfg.FIXED_LOT

    pips_at_risk = sl_distance / tick_size
    lot = risk_money / (pips_at_risk * tick_value)
    lot = max(sym_info.volume_min, round(lot, 2))
    lot = min(sym_info.volume_max, lot)

    return lot


def place_limit_order(symbol: str, signal_data: dict) -> bool:
    """
    Memasang Limit Order (Pending Order) berdasarkan data sinyal.

    Order type:
      - Sinyal BUY  → ORDER_TYPE_BUY_LIMIT  (entry di bawah harga market)
      - Sinyal SELL → ORDER_TYPE_SELL_LIMIT (entry di atas harga market)

    Expiry: ORDER_EXPIRY_SEC detik dari sekarang (default 60 detik = 1 candle M1)
    """
    if mt5 is None:
        logger.error("MT5 tidak tersedia, tidak bisa pasang order.")
        return False

    signal      = signal_data['signal']
    entry_price = signal_data['entry']
    sl_price    = signal_data['sl']
    tp_price    = signal_data['tp']
    sl_distance = signal_data['sl_distance']
    candle_type = signal_data['candle_type']

    lot = calculate_lot_size(symbol, sl_distance)

    # Tentukan tipe order
    if signal == "BUY":
        order_type = mt5.ORDER_TYPE_BUY_LIMIT
        type_str   = "BUY LIMIT"
    elif signal == "SELL":
        order_type = mt5.ORDER_TYPE_SELL_LIMIT
        type_str   = "SELL LIMIT"
    else:
        return False

    # Waktu kadaluarsa order (expiry)
    expiry_time = int(time.time()) + cfg.ORDER_EXPIRY_SEC

    request = {
        "action"      : mt5.TRADE_ACTION_PENDING,
        "symbol"      : symbol,
        "volume"      : lot,
        "type"        : order_type,
        "price"       : entry_price,
        "sl"          : sl_price,
        "tp"          : tp_price,
        "type_time"   : mt5.ORDER_TIME_SPECIFIED,  # order punya waktu kadaluarsa
        "expiration"  : expiry_time,
        "comment"     : f"MomBot|{candle_type[:8]}|{signal}",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        msg = (
            f"📋 *{type_str} TERPASANG*\n"
            f"Symbol : {symbol}\n"
            f"Candle : {candle_type}\n"
            f"Entry  : {entry_price}\n"
            f"SL     : {sl_price}\n"
            f"TP     : {tp_price}\n"
            f"Lot    : {lot}\n"
            f"Expiry : 60 detik"
        )
        logger.info(f"✅ {type_str} berhasil dipasang | Ticket: {result.order}")
        send_telegram(msg)
        return True
    else:
        logger.error(
            f"❌ Gagal pasang {type_str} | Retcode: {result.retcode} | "
            f"Comment: {result.comment}"
        )
        return False


def cancel_expired_orders(symbol: str):
    """
    Membatalkan semua pending order yang sudah melewati waktu expiry.
    MT5 biasanya membatalkan otomatis jika type_time=ORDER_TIME_SPECIFIED,
    fungsi ini sebagai fallback safety.
    """
    if mt5 is None:
        return

    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return

    now = int(time.time())
    for order in orders:
        if order.time_expiration > 0 and now > order.time_expiration:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order" : order.ticket,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"🗑️  Pending order #{order.ticket} dibatalkan (expired).")
            else:
                logger.warning(f"Gagal cancel order #{order.ticket}: {result.comment}")


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
    logger.info("  🚀 Robot Scalping Momentum M1 — XAUUSD  ")
    logger.info("=" * 60)

    if not connect_mt5():
        logger.critical("Gagal konek ke MT5. Robot berhenti.")
        return

    symbol = cfg.SYMBOL

    try:
        while True:
            # ── Tunggu candle berikutnya close ───────────────────────────────
            candle_close_time = wait_for_candle_close()
            logger.info(f"─── Candle close: {candle_close_time.strftime('%Y-%m-%d %H:%M:%S')} UTC ───")

            # ── Ambil data candle terbaru ─────────────────────────────────────
            df = get_candles(symbol, cfg.TIMEFRAME, cfg.CANDLES_NEEDED + 2)
            if df is None:
                logger.warning("Data candle tidak tersedia. Skip siklus ini.")
                continue

            # ── Bersihkan order yang sudah kadaluarsa ─────────────────────────
            cancel_expired_orders(symbol)

            # ── Cek apakah masih ada posisi/order terbuka ────────────────────
            open_pos = count_open_positions(symbol)
            pending  = count_pending_orders(symbol)

            if open_pos >= cfg.MAX_OPEN_POSITIONS:
                logger.info(f"⏸️  Posisi terbuka: {open_pos}. Menunggu posisi selesai.")
                continue

            if pending > 0:
                logger.info(f"⏸️  Masih ada {pending} pending order. Skip analisa.")
                continue

            # ── Analisa candle ────────────────────────────────────────────────
            signal_data = analyze_candle(df, cfg)

            if signal_data is None:
                logger.info("🔍 Tidak ada momentum candle terdeteksi. Standby...")
                continue

            # ── Ada sinyal → pasang Limit Order ──────────────────────────────
            logger.info(
                f"🎯 Sinyal: {signal_data['signal']} | "
                f"Tipe: {signal_data['candle_type']} | "
                f"Entry: {signal_data['entry']} | "
                f"SL: {signal_data['sl']} | TP: {signal_data['tp']}"
            )

            place_limit_order(symbol, signal_data)

    except KeyboardInterrupt:
        logger.info("\n⏹️  Robot dihentikan oleh user (Ctrl+C).")
        send_telegram("⛔ Robot Momentum M1 dihentikan secara manual.")

    except Exception as e:
        logger.exception(f"❌ Error tidak terduga: {e}")
        send_telegram(f"❌ Robot ERROR: {e}")

    finally:
        disconnect_mt5()
        logger.info("Robot selesai.")


# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    run()
