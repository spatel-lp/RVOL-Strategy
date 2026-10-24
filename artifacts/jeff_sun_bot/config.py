"""
Configuration – enhanced with ATR extension + VARS + leveraged preference
"""

# ====================== RISK MANAGEMENT ======================
RISK_PER_TRADE = 0.0020
MAX_POSITION_PCT = 0.28
MAX_OPEN_POSITIONS = 5
MAX_DAILY_NEW_TRADES = 3

RISK_PRESETS = {
    "conservative": 0.0015,
    "standard": 0.0020,
    "aggressive": 0.0030,
}

# ====================== ENTRY FILTERS ======================
MIN_ADR_PCT = 3.8
MAX_MARKET_CAP = 45_000_000_000
MIN_AVG_DOLLAR_VOL = 8_000_000
MIN_RVOL = 1.6
LOD_ATR_MAX = 0.58

MIN_RS_RANK = 70
MIN_VARS = 65                    # Volatility-Adjusted RS threshold

# ATR Extension filter (Jeff: avoid chasing already extended names)
# Jeff's hub page: hard rule - no entry (and nothing on the Focus List) beyond 4x ATR% from the 50-MA.
MAX_ATR_EXT_50 = 4.0
IDEAL_ATR_EXT_RANGE = (0.5, 3.5) # Prefer setups that are not too extended
STALK_MAX_ATR_EXT = 6.0          # Stalk list may hold names a bit further out; Focus/entry may not
# VERIFIED against jfsrev's "ATR% multiple from 50-MA" description (the script itself is closed-source):
#   A = ATR% = ATR / last price ; B = % gain from the 50-MA ; multiple = B / A
#   i.e. ((close / SMA50) - 1) / (ATR / close), ATR = Wilder/RMA(14) (Pine ta.atr).
# "atr_pct": the formula above (default, matches Jeff)
# "atr":     (close - MA) / ATR  -> plain ATR multiple (old behaviour, NOT his indicator)
ATR_EXT_MODE = "atr_pct"
MA_TREND = 200
SMA200_FALL_LOOKBACK = 20

MA_SHORT = 10
MA_MEDIUM = 20
MA_LONG = 50

# ====================== VCP ======================
VCP_LOOKBACK = 25
VCP_MIN_SCORE = 0.55

# ====================== ORH ======================
ORH_OPENING_MINUTES = 30
# Share of an average full day's volume already traded by the reclaim bar.
# ASSUMPTION (his FAQ mentions ~20% inside the first 30 min for some setups); tune from your journal.
ORH_MIN_DAYVOL_PCT = 0.20
ORH_MAX_LOD_ATR = 0.60

# ====================== PROFIT TAKING ======================
PARTIAL_1_R = 2.0
PARTIAL_SIZE = 0.33
DAY3_SIZE_REDUCTION = 0.33
TRAIL_MA = 10

# 3-stop: size to the FULL stop (=1R), but defend with three staged stops that each
# sell a third. Levels as a fraction of the way from entry down to the full stop.
# Full failure = (1/3)(0.33 + 0.67 + 1.0) = -0.67R. Daily-bar approximation: intraday
# timing of stops 1 and 2 (his "no confirmation in 1-2 hrs", "intraday support") is not modelled.
USE_THREE_STOP = True
THREE_STOP_LEVELS = (0.33, 0.67, 1.0)

# ATR extension based partials (Jeff style)
# Jeff: "start taking profits when positions exceed 7-10x the ATR% from the 50-SMA".
# The split across 7 / 8.5 / 10 is mine; he gives only the range. Each level fires once.
ATR_EXT_PARTIAL_LEVELS = [7.0, 8.5, 10.0]
ATR_EXT_PARTIAL_SIZE = 0.25

# ====================== BACKTEST / SIM ======================
INITIAL_EQUITY = 100_000
COMMISSION_PCT = 0.0005
SLIPPAGE_PCT = 0.0010

# ====================== LIVE SCAN ======================
LIVE_ALERT_FILE = "daily_focus_list.txt"
MIN_FOCUS_SCORE = 0.60

# ====================== MEAN REVERSION ======================
MR_MIN_SCORE = 0.45              # Minimum mean-reversion attractiveness
MR_MIN_STRETCH = -1.5            # At least this negative ATR_ext_50
MR_MAX_STRETCH = -4.5            # Not more extreme than this
MR_MIN_ADR = 2.5                 # Still want some volatility

# ====================== INTRADAY MEAN REVERSION ======================
IMR_MAX_RISK_ATR = 0.40          # Hard rule: ≤ 40% ATR from LoD
IMR_MIN_LOWER_WICK = 0.55        # Lower wick must be majority of the range
IMR_MAX_BODY = 0.35              # Small body relative to range

# ====================== UNIVERSE RULES ======================
# Jeff excludes biotechs from post-market scans (gap risk); exposure only via ETFs (LABU/LABD).
EXCLUDED_INDUSTRY_KEYWORDS = ("biotech",)
EXCLUDED_SYMBOLS = {"MRNA", "VKTX", "RXRX", "NVAX"}
LEVERAGED_UNIVERSE_SHARE = 0.25   # reserve this share of universe slots for leveraged ETFs

# ====================== PAPER TRADING AUTOMATION ======================
# Folders (override with env vars PAPER_STATE_DIR / PAPER_DOCS_DIR; the GitHub workflow does).
PAPER_STATE_DIR = "state"                 # paper_state.json + fundamentals cache
PAPER_DOCS_DIR = "docs"                   # dashboard HTML (index.html)
SCAN_NOT_BEFORE_ET = (16, 15)             # post-close scan only after the daily bar is final
INTRADAY_WINDOW_ET = ((10, 0), (15, 50))  # intraday job only acts inside this window
# ASSUMPTION (not from Jeff): no new paper entries after this time; late entries leave no room to be right.
PAPER_LAST_ENTRY_ET = (15, 0)
FOCUS_MAX_AGE_DAYS = 4                    # ignore a focus list older than this (long weekends)
FUNDAMENTALS_TTL_DAYS = 7                 # yfinance .info is slow and rate-limited; cache it

# ====================== ROUND 3: VALIDATION, FILTERS, PORTFOLIO RISK ======================
# Every number below that is not from Jeff's hub page is MY assumption. Tune it from your own journal.

# ---- Rules-based universe (point-in-time snapshots; replaces the hand-picked list)
UNIVERSE_MODE = "rules"               # "rules" | "handpicked"  (env PAPER_UNIVERSE_MODE overrides)
UNIVERSE_SIZE = 150
UNIVERSE_MIN_DOLLAR_VOL = 40_000_000  # 20-day average dollar volume
UNIVERSE_MIN_ADR = 3.5                # Jeff prefers higher ADR%
UNIVERSE_MIN_PRICE = 8.0
UNIVERSE_LEV_MIN_DOLLAR_VOL = 10_000_000
UNIVERSE_REFRESH_DAYS = 7
UNIVERSE_CHUNK = 150                  # symbols per yfinance batch download

# ---- Entry filters from Jeff's hard rules
USE_GAP_RESISTANCE = False            # DEFERRED: code exists in filters.py but is not wired in yet
GAP_RES_LOOKBACK = 60                 # look for unfilled gap-downs this many bars back
GAP_RES_ATR = 1.0                     # price within this many ATR below the gap = "into resistance"
LINEARITY_DAYS = 40
MIN_LINEARITY = 0.60                  # R^2 of log-price trend; low = choppy mover, "low win rate and drawdowns"

# ---- Portfolio risk
LEVERAGE_X = {                        # effective leverage; default for unlisted leveraged ETFs is 2
    "SOXL": 3, "TQQQ": 3, "TECL": 3, "FNGU": 3, "TNA": 3, "SPXL": 3, "UPRO": 3, "TMF": 3,
    "LABU": 3, "HIBL": 3, "CURE": 3, "DFEN": 3, "WEBL": 3, "FAS": 3, "CWEB": 2,
}
LEVERAGED_RISK_MULT = 0.5             # leveraged ETFs get half the normal risk (gap / decay / tail risk)
MAX_LEVERAGED_EXPOSURE_PCT = 0.60     # sum(position value x leverage) of leveraged ETFs, as share of equity
MAX_PER_INDUSTRY = 2                  # five semis are one bet
MAX_PER_SECTOR = 3
MAX_TOTAL_OPEN_RISK_PCT = 0.010       # sum of (price - stop) x shares across all open positions
# (sector, industry) cluster for leveraged / single-stock ETFs so they count with what they track
ETF_CLUSTER = {
    "SOXL": ("Technology", "Semiconductors"), "NVDL": ("Technology", "Semiconductors"),
    "AMDL": ("Technology", "Semiconductors"), "TQQQ": ("Index", "Nasdaq-100"),
    "TECL": ("Technology", "Tech Basket"), "FNGU": ("Technology", "Mega-cap Tech"),
    "TNA": ("Index", "Small-cap"), "SPXL": ("Index", "S&P 500"), "UPRO": ("Index", "S&P 500"),
    "TMF": ("Rates", "Long Treasuries"), "LABU": ("Healthcare", "Biotechnology"),
    "CURE": ("Healthcare", "Healthcare Basket"), "DFEN": ("Industrials", "Aerospace & Defense"),
    "HIBL": ("Index", "High Beta"), "WEBL": ("Communication Services", "Internet"),
    "CWEB": ("Communication Services", "China Internet"), "FAS": ("Financial Services", "Financials Basket"),
    "TSLL": ("Consumer Cyclical", "Auto Manufacturers"), "AAPU": ("Technology", "Consumer Electronics"),
    "AAPB": ("Technology", "Consumer Electronics"), "CRMG": ("Technology", "Software - Application"),
    "MSFU": ("Technology", "Software - Infrastructure"), "METU": ("Communication Services", "Internet Content"),
}

# ---- Drawdown brakes ("never lose two weeks of gains in a day")
DD_TIERS = ((0.10, 0.0), (0.06, 0.25), (0.03, 0.5))   # (drawdown from equity peak, risk multiplier), largest first
DAILY_LOSS_LIMIT_PCT = 0.005          # stop opening new trades once the day is down this much from yesterday's close

# ---- Time-adjusted volume (replaces the flat 20%-of-day check when a profile is available)
ORH_MIN_TIME_RVOL = 1.5               # today's volume vs what is typical BY THIS TIME of day
PROFILE_SESSIONS = 15
PROFILE_MIN_SESSIONS = 5

# ---- Exit variants (exit_lab.py compares them on the same signals)
EXIT_VARIANT = "ma10"                 # "ma10" | "ema21" | "atr_trail"
DAY3_BREAKEVEN = True
ATR_TRAIL_MULT = 3.0                  # atr_trail: exit on a close this many ATR under the highest close since entry

# ---- Fill model: slippage scales with volatility and with how much of the day's volume you are
SLIP_ATR_FRAC = 0.02                  # base slippage = 2% of ATR% (e.g. ATR 5% of price -> 0.10%)
SLIP_IMPACT = 0.50                    # + this x participation (shares / avg daily volume)
SLIP_CAP = 0.015

# ---- Reject / skip log and forward tracking (does a filter protect you or cost you winners?)
SIGNAL_MAX_PER_DAY = 40
SIGNAL_KEEP_DAYS = 150
FORWARD_DAYS = (1, 3, 5, 10)
AUDIT_MIN_N = 30                      # fewer signals than this = "too few to judge"

# ---- Evidence
TARGET_TRADES = 100
BOOTSTRAP_N = 4000

# ---- Data health
DATA_EXTREME_MOVE = 0.35              # one-day move beyond this on a non-leveraged name = possible split / bad tick
DATA_GAP_BDAYS = 4
CROSSCHECK_EVERY_DAYS = 7
CROSSCHECK_SAMPLE = 12
CROSSCHECK_TOL = 0.015                # 1.5% close difference vs Stooq before flagging

# ---- Notifications (all optional; configured with environment variables, see README_AUTOMATION.md)
NOTIFY_KINDS = ("ENTRY", "STOP", "CLOSE", "ERROR", "DIGEST", "WARN")
SLIP_FLOOR = 0.0003                   # never model less than 0.03% slippage

# ---- Return-correlation cap (on top of the industry / sector cluster caps). All thresholds are MY assumptions.
USE_CORRELATION_CAP = True
CORR_LOOKBACK = 60                    # daily returns used to measure correlation
CORR_MIN_OBS = 40                     # fewer overlapping days than this = no opinion (never blocks)
CORR_PAIR_BLOCK = 0.85                # refuse a name this correlated with any single open position
CORR_HIGH = 0.70                      # "highly correlated" for the count below
MAX_HIGHLY_CORRELATED = 2             # refuse once this many open positions are already highly correlated with it

# ====================== ROUND 4: EARNINGS FILTER, REGIME GATE, ALPACA ======================
# ---- Earnings filter (scanner): a name with earnings inside the window cannot be on the Focus list
USE_EARNINGS_FILTER = True
EARNINGS_WINDOW_DAYS = 7              # block when the next report is 0..7 calendar days away
EARNINGS_CACHE_DAYS = 3
EARNINGS_RETRY_DAYS = 1               # a failed lookup is retried after this long (and never blocks)

# ---- Market regime gate (top-down read, applied to every new entry). Thresholds are MY assumptions.
USE_REGIME_GATE = True
REGIME_MIN_NAMES = 20                 # fewer names than this in the universe = no opinion
REGIME_BREADTH_OFF = 30.0             # % of universe above its 20-MA below this = risk-off (no new entries)
REGIME_BREADTH_ON = 50.0              # below this = neutral (half risk)
REGIME_INDEX_STRETCH = 6.0            # SPY ATR% multiple from the 50-MA at/above this = stretched (half risk)
REGIME_UP_DAYS = 5                    # this many SPY up-closes in a row = stretched
REGIME_MULT = {"risk-on": 1.0, "neutral": 0.5, "stretched": 0.5, "risk-off": 0.0, "unknown": 1.0}
