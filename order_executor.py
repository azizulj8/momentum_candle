# =============================================================================
# order_executor.py — Modul Eksekusi Order Buy & Sell ke MetaTrader 5
# =============================================================================

import time
import logging
import requests

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import config as cfg

logger = logging.getLogger("OrderExecutor")

# =============================================================================
# HELPER: Pip size & filling mode
# =============================================================================

def get_pip_size(symbol: str) -> float:
    """Pip size XAUUSD = 0.10, Forex pair = 0.00001."""
    if mt5 is None:
        return 0.10
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.10
    # XAUUSD: digits=2, pip=0.10 | EURUSD: digits=5, pip=0.00010
    return 10 ** -(info.digits - 1)


def get_filling_mode(symbol: str) -> int:
    """Deteksi filling mode yang didukung broker secara otomatis."""
    if mt5 is None:
        return mt5.ORDER_FILLING_IOC if mt5 else 1
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    filling = info.filling_mode
    # Cek urutan preferensi: FOK → IOC → RETURN
    if filling & mt5.SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    elif filling & mt5.SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    else:
        return mt5.ORDER_FILLING_RETURN


def round_price(price: float, symbol: str) -> float:
    """Bulatkan harga sesuai jumlah digit simbol."""
    if mt5 is None:
        return round(price, 2)
    info = mt5.symbol_info(symbol)
    digits = info.digits if info else 2
    return round(price, digits)


def get_lot(symbol: str, sl_distance: float) -> float:
    """Hitung lot: fixed atau berbasis % risiko dari balance."""
    if cfg.USE_FIXED_LOT:
        return cfg.FIXED_LOT
    if mt5 is None or sl_distance == 0:
        return cfg.FIXED_LOT
    account  = mt5.account_info()
    sym_info = mt5.symbol_info(symbol)
    if account is None or sym_info is None:
        return cfg.FIXED_LOT
    risk_money   = account.balance * cfg.RISK_PERCENT
    tick_value   = sym_info.trade_tick_value
    tick_size    = sym_info.trade_tick_size
    if tick_size == 0 or tick_value == 0:
        return cfg.FIXED_LOT
    ticks_at_risk = sl_distance / tick_size
    lot = risk_money / (ticks_at_risk * tick_value)
    lot = round(max(sym_info.volume_min, min(sym_info.volume_max, lot)), 2)
    return lot


# =============================================================================
# BAGIAN 1: PASANG LIMIT ORDER (PENDING ORDER)
# =============================================================================

def place_buy_limit(symbol: str, entry: float, sl: float, tp: float,
                    candle_type: str = "") -> dict:
    """
    Memasang BUY LIMIT order.
    Entry harus di BAWAH harga market saat ini (menunggu pullback).

    Returns:
        dict: {'success': bool, 'ticket': int, 'error': str}
    """
    if mt5 is None:
        return {"success": False, "ticket": 0, "error": "MT5 tidak tersedia"}

    sl_distance = abs(entry - sl)
    lot         = get_lot(symbol, sl_distance)
    filling     = get_filling_mode(symbol)
    expiry_time = int(time.time()) + cfg.ORDER_EXPIRY_SEC

    entry = round_price(entry, symbol)
    sl    = round_price(sl, symbol)
    tp    = round_price(tp, symbol)

    request = {
        "action"      : mt5.TRADE_ACTION_PENDING,
        "symbol"      : symbol,
        "volume"      : lot,
        "type"        : mt5.ORDER_TYPE_BUY_LIMIT,
        "price"       : entry,
        "sl"          : sl,
        "tp"          : tp,
        "type_time"   : mt5.ORDER_TIME_SPECIFIED,
        "expiration"  : expiry_time,
        "comment"     : f"MomBot|BUY|{candle_type[:8]}",
        "type_filling": filling,
        "deviation"   : 10,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"[OK] BUY LIMIT dipasang | Ticket: {result.order} | "
            f"Entry: {entry} | SL: {sl} | TP: {tp} | Lot: {lot}"
        )
        _notify(
            f"*BUY LIMIT DIPASANG*\n"
            f"Symbol : {symbol}\n"
            f"Candle : {candle_type}\n"
            f"Entry  : {entry}\n"
            f"SL     : {sl}\n"
            f"TP     : {tp}\n"
            f"Lot    : {lot}\n"
            f"Expiry : {cfg.ORDER_EXPIRY_SEC}s"
        )
        return {"success": True, "ticket": result.order, "error": ""}
    else:
        msg = f"retcode={result.retcode} | {result.comment}"
        logger.error(f"[FAIL] BUY LIMIT gagal | {msg}")
        return {"success": False, "ticket": 0, "error": msg}


def place_sell_limit(symbol: str, entry: float, sl: float, tp: float,
                     candle_type: str = "") -> dict:
    """
    Memasang SELL LIMIT order.
    Entry harus di ATAS harga market saat ini (menunggu retest).

    Returns:
        dict: {'success': bool, 'ticket': int, 'error': str}
    """
    if mt5 is None:
        return {"success": False, "ticket": 0, "error": "MT5 tidak tersedia"}

    sl_distance = abs(sl - entry)
    lot         = get_lot(symbol, sl_distance)
    filling     = get_filling_mode(symbol)
    expiry_time = int(time.time()) + cfg.ORDER_EXPIRY_SEC

    entry = round_price(entry, symbol)
    sl    = round_price(sl, symbol)
    tp    = round_price(tp, symbol)

    request = {
        "action"      : mt5.TRADE_ACTION_PENDING,
        "symbol"      : symbol,
        "volume"      : lot,
        "type"        : mt5.ORDER_TYPE_SELL_LIMIT,
        "price"       : entry,
        "sl"          : sl,
        "tp"          : tp,
        "type_time"   : mt5.ORDER_TIME_SPECIFIED,
        "expiration"  : expiry_time,
        "comment"     : f"MomBot|SELL|{candle_type[:8]}",
        "type_filling": filling,
        "deviation"   : 10,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"[OK] SELL LIMIT dipasang | Ticket: {result.order} | "
            f"Entry: {entry} | SL: {sl} | TP: {tp} | Lot: {lot}"
        )
        _notify(
            f"*SELL LIMIT DIPASANG*\n"
            f"Symbol : {symbol}\n"
            f"Candle : {candle_type}\n"
            f"Entry  : {entry}\n"
            f"SL     : {sl}\n"
            f"TP     : {tp}\n"
            f"Lot    : {lot}\n"
            f"Expiry : {cfg.ORDER_EXPIRY_SEC}s"
        )
        return {"success": True, "ticket": result.order, "error": ""}
    else:
        msg = f"retcode={result.retcode} | {result.comment}"
        logger.error(f"[FAIL] SELL LIMIT gagal | {msg}")
        return {"success": False, "ticket": 0, "error": msg}


# =============================================================================
# BAGIAN 2: EKSEKUSI MARKET ORDER (LANGSUNG MASUK)
# =============================================================================

def place_buy_market(symbol: str, sl: float, tp: float,
                     candle_type: str = "") -> dict:
    """
    Market BUY langsung (tidak menunggu pullback).
    Digunakan jika sinyal Marubozu sangat kuat dan tidak ada waktu tunggu.
    """
    if mt5 is None:
        return {"success": False, "ticket": 0, "error": "MT5 tidak tersedia"}

    tick        = mt5.symbol_info_tick(symbol)
    ask         = tick.ask
    sl_distance = abs(ask - sl)
    lot         = get_lot(symbol, sl_distance)
    filling     = get_filling_mode(symbol)

    sl = round_price(sl, symbol)
    tp = round_price(tp, symbol)

    request = {
        "action"      : mt5.TRADE_ACTION_DEAL,
        "symbol"      : symbol,
        "volume"      : lot,
        "type"        : mt5.ORDER_TYPE_BUY,
        "price"       : ask,
        "sl"          : sl,
        "tp"          : tp,
        "comment"     : f"MomBot|BUYMKT|{candle_type[:6]}",
        "type_filling": filling,
        "deviation"   : 20,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"[OK] BUY MARKET terisi | Ticket: {result.order} | "
            f"Price: {result.price} | SL: {sl} | TP: {tp}"
        )
        _notify(
            f"*BUY MARKET TERISI*\n"
            f"Symbol : {symbol}\n"
            f"Price  : {result.price}\n"
            f"SL     : {sl}\n"
            f"TP     : {tp}\n"
            f"Lot    : {lot}"
        )
        return {"success": True, "ticket": result.order, "error": ""}
    else:
        msg = f"retcode={result.retcode} | {result.comment}"
        logger.error(f"[FAIL] BUY MARKET gagal | {msg}")
        return {"success": False, "ticket": 0, "error": msg}


def place_sell_market(symbol: str, sl: float, tp: float,
                      candle_type: str = "") -> dict:
    """
    Market SELL langsung.
    """
    if mt5 is None:
        return {"success": False, "ticket": 0, "error": "MT5 tidak tersedia"}

    tick        = mt5.symbol_info_tick(symbol)
    bid         = tick.bid
    sl_distance = abs(sl - bid)
    lot         = get_lot(symbol, sl_distance)
    filling     = get_filling_mode(symbol)

    sl = round_price(sl, symbol)
    tp = round_price(tp, symbol)

    request = {
        "action"      : mt5.TRADE_ACTION_DEAL,
        "symbol"      : symbol,
        "volume"      : lot,
        "type"        : mt5.ORDER_TYPE_SELL,
        "price"       : bid,
        "sl"          : sl,
        "tp"          : tp,
        "comment"     : f"MomBot|SELLMKT|{candle_type[:6]}",
        "type_filling": filling,
        "deviation"   : 20,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"[OK] SELL MARKET terisi | Ticket: {result.order} | "
            f"Price: {result.price} | SL: {sl} | TP: {tp}"
        )
        _notify(
            f"*SELL MARKET TERISI*\n"
            f"Symbol : {symbol}\n"
            f"Price  : {result.price}\n"
            f"SL     : {sl}\n"
            f"TP     : {tp}\n"
            f"Lot    : {lot}"
        )
        return {"success": True, "ticket": result.order, "error": ""}
    else:
        msg = f"retcode={result.retcode} | {result.comment}"
        logger.error(f"[FAIL] SELL MARKET gagal | {msg}")
        return {"success": False, "ticket": 0, "error": msg}


# =============================================================================
# BAGIAN 3: MANAJEMEN POSISI AKTIF
# =============================================================================

def close_position(ticket: int, symbol: str) -> bool:
    """
    Menutup posisi aktif berdasarkan ticket number.
    Otomatis mendeteksi apakah BUY atau SELL dan melakukan counter-order.
    """
    if mt5 is None:
        return False

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        logger.warning(f"[WARN] Posisi #{ticket} tidak ditemukan.")
        return False

    pos     = positions[0]
    tick    = mt5.symbol_info_tick(symbol)
    filling = get_filling_mode(symbol)

    # Counter-order: BUY position → tutup dengan SELL, dan sebaliknya
    if pos.type == mt5.POSITION_TYPE_BUY:
        close_type  = mt5.ORDER_TYPE_SELL
        close_price = tick.bid
    else:
        close_type  = mt5.ORDER_TYPE_BUY
        close_price = tick.ask

    request = {
        "action"      : mt5.TRADE_ACTION_DEAL,
        "symbol"      : symbol,
        "volume"      : pos.volume,
        "type"        : close_type,
        "position"    : ticket,
        "price"       : close_price,
        "comment"     : "MomBot|CLOSE",
        "type_filling": filling,
        "deviation"   : 20,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        profit = pos.profit
        logger.info(f"[OK] Posisi #{ticket} ditutup | PnL: {profit:.2f}")
        _notify(f"*POSISI DITUTUP*\nTicket: {ticket}\nPnL: {profit:.2f}")
        return True
    else:
        logger.error(f"[FAIL] Gagal tutup posisi #{ticket}: {result.comment}")
        return False


def modify_sl_tp(ticket: int, symbol: str,
                 new_sl: float = None, new_tp: float = None) -> bool:
    """
    Modifikasi Stop Loss dan/atau Take Profit dari posisi aktif.
    Berguna untuk trailing stop manual.
    """
    if mt5 is None:
        return False

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        logger.warning(f"[WARN] Posisi #{ticket} tidak ditemukan untuk modifikasi.")
        return False

    pos    = positions[0]
    new_sl = round_price(new_sl if new_sl is not None else pos.sl, symbol)
    new_tp = round_price(new_tp if new_tp is not None else pos.tp, symbol)

    request = {
        "action"  : mt5.TRADE_ACTION_SLTP,
        "symbol"  : symbol,
        "position": ticket,
        "sl"      : new_sl,
        "tp"      : new_tp,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"[OK] SL/TP diperbarui | #{ticket} | SL: {new_sl} | TP: {new_tp}")
        return True
    else:
        logger.error(f"[FAIL] Gagal modif SL/TP #{ticket}: {result.comment}")
        return False


def cancel_order(ticket: int) -> bool:
    """Membatalkan pending order berdasarkan ticket."""
    if mt5 is None:
        return False

    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    result  = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"[CANCEL] Pending order #{ticket} dibatalkan.")
        return True
    else:
        logger.warning(f"[WARN] Gagal cancel order #{ticket}: {result.comment}")
        return False


def cancel_all_pending(symbol: str) -> int:
    """Membatalkan semua pending order untuk simbol tertentu. Returns jumlah yang dibatalkan."""
    if mt5 is None:
        return 0
    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return 0
    count = sum(1 for o in orders if cancel_order(o.ticket))
    return count


# =============================================================================
# BAGIAN 4: QUERY STATUS POSISI & ORDER
# =============================================================================

def get_open_positions(symbol: str) -> list:
    """Mengembalikan list posisi aktif untuk simbol tertentu."""
    if mt5 is None:
        return []
    positions = mt5.positions_get(symbol=symbol)
    return list(positions) if positions else []


def get_pending_orders(symbol: str) -> list:
    """Mengembalikan list pending order untuk simbol tertentu."""
    if mt5 is None:
        return []
    orders = mt5.orders_get(symbol=symbol)
    return list(orders) if orders else []


def is_order_filled(ticket: int) -> bool:
    """
    Cek apakah pending order sudah terisi (menjadi posisi aktif).
    Caranya: cari ticket tersebut di daftar posisi aktif.
    """
    if mt5 is None:
        return False
    positions = mt5.positions_get(ticket=ticket)
    return len(positions) > 0 if positions else False


def get_account_info() -> dict:
    """Mengembalikan info akun: balance, equity, margin, free_margin."""
    if mt5 is None:
        return {}
    acc = mt5.account_info()
    if acc is None:
        return {}
    return {
        "balance"     : acc.balance,
        "equity"      : acc.equity,
        "margin"      : acc.margin,
        "free_margin" : acc.margin_free,
        "profit"      : acc.profit,
        "currency"    : acc.currency,
    }


# =============================================================================
# BAGIAN 5: FUNGSI DISPATCHER UTAMA
# =============================================================================

def execute_signal(signal_data: dict, symbol: str, use_market_order: bool = False) -> dict:
    """
    Fungsi utama yang dipanggil dari bot.py setelah sinyal terdeteksi.

    Args:
        signal_data      : dict hasil dari analyze_candle() di candle_detector.py
        symbol           : nama simbol, misal "XAUUSDm"
        use_market_order : True = market order langsung, False = limit order (default)

    Returns:
        dict: {'success': bool, 'ticket': int, 'order_type': str, 'error': str}
    """
    signal      = signal_data.get("signal")
    entry       = signal_data.get("entry")
    sl          = signal_data.get("sl")
    tp          = signal_data.get("tp")
    candle_type = signal_data.get("candle_type", "")

    if signal not in ("BUY", "SELL"):
        return {"success": False, "ticket": 0, "order_type": "", "error": "Sinyal tidak valid"}

    logger.info(
        f"[EXEC] {signal} | {candle_type} | "
        f"Entry: {entry} | SL: {sl} | TP: {tp} | "
        f"Mode: {'Market' if use_market_order else 'Limit'}"
    )

    if use_market_order:
        # Mode market order: langsung masuk tanpa tunggu pullback
        if signal == "BUY":
            result = place_buy_market(symbol, sl, tp, candle_type)
        else:
            result = place_sell_market(symbol, sl, tp, candle_type)
        result["order_type"] = f"{signal}_MARKET"
    else:
        # Mode limit order: pasang pending order, tunggu harga pullback/retest
        if signal == "BUY":
            result = place_buy_limit(symbol, entry, sl, tp, candle_type)
        else:
            result = place_sell_limit(symbol, entry, sl, tp, candle_type)
        result["order_type"] = f"{signal}_LIMIT"

    return result


# =============================================================================
# HELPER INTERNAL: Notifikasi Telegram
# =============================================================================

def _notify(message: str):
    """Kirim notifikasi Telegram (internal helper)."""
    if not cfg.TELEGRAM_ENABLED:
        return
    try:
        import requests as req
        url  = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": cfg.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        req.post(url, data=data, timeout=10)
    except Exception as e:
        logger.warning(f"[WARN] Telegram error: {e}")
