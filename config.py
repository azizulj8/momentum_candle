# =============================================================================
# config.py — Konfigurasi Robot Scalping Momentum M1 (XAUUSD)
# =============================================================================
# ⚠️  JANGAN pernah membagikan file ini jika berisi password akun Live!

# ---------------------------------------------------------------------------
# 1. Koneksi MetaTrader 5
# ---------------------------------------------------------------------------
MT5_ACCOUNT  = 12345678             # Ganti: nomor akun Demo Anda
MT5_PASSWORD = "your_password"      # Ganti: password akun Demo Anda
MT5_SERVER   = "Exness-MT5Trial"    # Ganti: nama server broker Anda

# ---------------------------------------------------------------------------
# 2. Instrumen & Timeframe
# ---------------------------------------------------------------------------
SYMBOL     = "XAUUSDm"   # Simbol emas (sesuaikan suffix broker, mis. XAUUSDm / XAUUSD)
TIMEFRAME  = "M1"        # Timeframe: 1 Menit (scalping)
CANDLES_NEEDED = 100     # Jumlah candle historis yang diambil untuk kalkulasi ATR/EMA

# ---------------------------------------------------------------------------
# 3. Parameter Deteksi Momentum Candle
# ---------------------------------------------------------------------------
ATR_PERIOD          = 14    # Periode ATR untuk mengukur volatilitas rata-rata
EMA_PERIOD          = 20    # Filter tren: hanya BUY di atas EMA, SELL di bawah EMA
USE_EMA_FILTER      = True  # True = aktifkan filter tren EMA

# Multiplier body terhadap ATR
MARUBOZU_BODY_MULT  = 1.5   # Body >= 1.5x ATR → Marubozu (sinyal terkuat)
IMPULSE_BODY_MULT   = 1.2   # Body >= 1.2x ATR → Impulse (sinyal kuat)

# Batas maksimum wick (sebagai % dari total range candle, 0.0 – 1.0)
MARUBOZU_MAX_WICK   = 0.05  # Marubozu: wick maks 5% dari range
IMPULSE_MAX_WICK    = 0.20  # Impulse: wick maks 20% dari range

# Parameter Pin Bar / Hammer / Shooting Star
PINBAR_MIN_WICK     = 0.60  # Lower/upper wick minimal 60% dari total range
PINBAR_MAX_OPP_WICK = 0.10  # Wick berlawanan maksimal 10% dari total range

# ---------------------------------------------------------------------------
# 4. Strategi Limit Order (Pending Order)
# ---------------------------------------------------------------------------
# Setelah candle sinyal close, robot pasang Limit Order di candle berikutnya.
# Entry dihitung mundur dari Close sebesar RETRACE_RATIO × Body candle.
RETRACE_RATIO    = 0.38   # Pullback entry: 38% dari body candle sinyal (Fibonacci)
ORDER_EXPIRY_SEC = 60     # Order otomatis dibatalkan setelah 60 detik (1 candle M1)

# Stop Loss: dihitung dari High/Low candle sinyal + buffer
SL_BUFFER_PIPS   = 5      # Buffer SL dalam pips di luar High/Low candle sinyal

# Risk:Reward ratio untuk kalkulasi Take Profit
RR_RATIO         = 1.5    # TP = Entry ± (SL distance × RR_RATIO)

# ---------------------------------------------------------------------------
# 5. Manajemen Lot / Volume
# ---------------------------------------------------------------------------
USE_FIXED_LOT    = True   # True = lot tetap, False = kalkulasi risiko otomatis
FIXED_LOT        = 0.01   # Ukuran lot tetap
RISK_PERCENT     = 0.01   # Risiko per trade: 1% dari balance (aktif jika USE_FIXED_LOT=False)

# Batasan posisi terbuka
MAX_OPEN_POSITIONS = 1    # Maksimal 1 posisi terbuka sekaligus untuk XAUUSD

# ---------------------------------------------------------------------------
# 6. Notifikasi Telegram
# ---------------------------------------------------------------------------
TELEGRAM_ENABLED  = True
TELEGRAM_BOT_TOKEN = "8616880679:AAHqAtJr_zQsg7P9XhsfHEW4n9Ee9Z-vm2Q"
TELEGRAM_CHAT_ID   = "6844797994"

# ---------------------------------------------------------------------------
# 7. Logging
# ---------------------------------------------------------------------------
LOG_FILE = "momentum_bot.log"  # Nama file log
LOG_LEVEL = "INFO"             # DEBUG / INFO / WARNING / ERROR
