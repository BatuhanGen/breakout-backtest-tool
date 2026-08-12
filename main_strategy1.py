import csv
import itertools
import json
import pandas as pd
import random
import re
import subprocess
import sys
import tempfile
import time
import traceback
import pickle
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator
import multiprocessing as _mp

# -----------------------------------------------------------------------------
# Strategy configuration (simple)
# -----------------------------------------------------------------------------
# Quick user examples:
# 1) Solo backtest only:
#    GRID_SEARCH_ENABLED = False
#    TRAILING_SL_ENABLED = True
#    TRAILING_SL_ACTIVATE_R = 1.0
#    TRAILING_SL_DISTANCE_R = 0.75
# 2) Grid search trailing SL on/off:
#    GRID_SEARCH_ENABLED = True
#    GRID_TRAILING_SL_ENABLED_OPTIONS = [False, True]
# 3) Break-even must stay logical:
#    BREAKEVEN_TRIGGER_R = 1.0 and BREAKEVEN_OFFSET = 0.25 is valid.
#    BREAKEVEN_TRIGGER_R = 0.5 and BREAKEVEN_OFFSET = 0.5 is skipped/invalid
#    because the new SL would be placed at the exact trigger level.
GUI_ENABLED = False  # False: running main_strategy.py directly skips the GUI and uses GRID_SEARCH_ENABLED.
# Data file (relative paths are resolved from this script's directory)
DATA_FILE = 'ohlc_data/US100data/NDX100/NDX100_M1.csv'  # path to your CSV file with DateTime, Open, High, Low, Close columns
# DATA_FILE = 'ohlc_data/UK100data/UK100_M1.csv'
# DATA_FILE = 'ohlc_data/US100data/NAS100/NAS100_M5.csv'
# DATA_FILE = 'ohlc_data/US100data/NDX100/NDX100_M1.csv'
DATA_TIME_CONVENTION = 'open'  # Dataset DateTime meaning: bar open time

# Bar timestamp convention (for strategy logic)
# 'open'  = DateTime is bar open time (common for broker/MT data)
# 'close' = DateTime is bar close time
BAR_TIME_MODE = 'open'  # 'open' or 'close'

# Range window
RANGE_START_HOUR = 14
RANGE_START_MINUTE = 30
RANGE_END_HOUR = 15
RANGE_END_MINUTE = 00
# Trade session end (forced close)
TRADE_END_HOUR = 19
TRADE_END_MINUTE = 0

# SL/TP logic
# Used when SL_PLACEMENT='distance': SL distance = breakout candle range * SL_MULTIPLIER
# Used when SL_PLACEMENT='range': SL distance = range * SL_MULTIPLIER
SL_MULTIPLIER = 0.5
RR_RATIO = 3.0 # TP distance = SL distance * RR

# Risk / balance
STARTING_BALANCE = 5000.0
FIXED_RISK_PER_TRADE = 50
RISK_MODE = 'percent'  # 'fixed' or 'percent'
RISK_PERCENT_PER_TRADE = 0.4 # percent of current equity per trade when RISK_MODE='percent'

# Prop-safe dynamic sizing layer. It wraps the existing RISK_MODE calculation;
# it never changes entry, BE or trailing-stop logic.
#
# enabled=False: use the original normal risk calculation exactly as before.
# enabled=True : choose only 'martingale', 'anti_martingale', or
#                'anti_martingale_ladder'.
RISK_SETTINGS = {
    'enabled': False,
    'mode': 'martingale',  # 'martingale', 'anti_martingale', or 'anti_martingale_ladder'
    'base_risk' : 20.0,
    'min_risk': 15.0,             # user-controlled currency floor
    'max_risk': 50.0,            # user-controlled currency ceiling
    'multiplier': 1.25,          # martingale / classic anti-martingale multiplier
    'win_step': 15.0,            # ladder: add this amount after a win
    'loss_multiplier': 0.50,     # ladder: after the base risk, multiply down after losses
    'enforce_prop_limits': False,
    'prop_safety_buffer_pct': 10.0,  # reserve this share of each remaining DD limit
}

# Equity chart validation.  CURVE_SPLIT_DATE takes precedence; otherwise the
# chronological trade split below is used.  The split is diagnostic only and
# therefore cannot leak future data into order generation.
EQUITY_IS_OOS_ENABLED = True
EQUITY_IS_OOS_TRAIN_PCT = 0.80
EQUITY_IS_OOS_SHADE_ALPHA = 0.10

# Edge-quality gates.  These emit an explicit PASS / REVIEW verdict in the
# report; they do not optimise parameters or alter executed trades.
EDGE_GUARD_ENABLED = True
EDGE_GUARD_MIN_TRADES_PER_SEGMENT = 20
EDGE_GUARD_MIN_OOS_PF = 1.05
EDGE_GUARD_MAX_OOS_DD_PCT = 8.0

# Data range filter
START_DATE = '2020-01-01' # e.g. '2024-01-01' (inclusive). None = no start filter

# Execution
ALLOW_SAME_BAR_FILL = False
MAX_TRADES_PER_DAY = 1
PENDING_EXPIRY_BARS = 100 # limit order expires after X bars
ONE_SIGNAL_PER_DAY = True
MIN_STOP_DISTANCE = 1e-9
PRICE_TOUCH_TOLERANCE = 1e-8
PENDING_TOUCH_MODE = 'wick'  # 'wick' or 'close'
CANCEL_PENDING_ON_OPPOSITE_BREAKOUT = True
CLOSE_POSITION_ON_DAY_CHANGE = True
INTRABAR_EXIT_POLICY = 'worst_case'  # 'worst_case', 'best_case', 'tp_first', 'sl_first'
FILL_BAR_EXIT_POLICY = 'conservative'  # 'conservative' or 'ohlc': conservative avoids TP on unknown fill-bar order
BACKTEST_COST_PER_TRADE = 0.0  # fixed round-trip cost in account currency
BACKTEST_COST_POINTS = 0.1    # round-trip spread/slippage cost in price units, multiplied by position_size
EXECUTION_REALISM_REQUIRED = True  # warns when neither spread nor slippage/commission is modelled

# Break-even logic (move SL to entry after favorable move)
BREAKEVEN_ENABLED = False
BREAKEVEN_TRIGGER_R = 1.0 # move to BE after price moves >= this R
BREAKEVEN_OFFSET = 0.0 # offset in R (0.0 = exact entry, 0.25 = lock +0.25R)
BREAKEVEN_TRIGGER_MODE = 'wick'  # 'wick' or 'close'
BREAKEVEN_ALLOW_SAME_BAR = False  # if True, can move SL within the trigger bar
BREAKEVEN_REQUIRE_OFFSET_BELOW_TRIGGER = True  # True skips invalid pairs like trigger=0.5, offset=0.5

# Trailing SL logic (optional, applied after a bar closes so OHLC order is not over-optimized)
TRAILING_SL_ENABLED = False
TRAILING_SL_ACTIVATE_R = 1.0   # start trailing after price moves this many R in profit
TRAILING_SL_DISTANCE_R = 0.75  # SL trails this many R behind high/low or close
TRAILING_SL_STEP_R = 0.25      # move SL only if improvement is at least this many R; 0 = every improvement
TRAILING_SL_TRIGGER_MODE = 'wick'  # 'wick' uses high/low, 'close' uses close only
TRAILING_SL_LOCK_BREAKEVEN = True  # when active, never trail below entry for LONG or above entry for SHORT
TRAILING_SL_CAP_AT_TP = True       # keep trailed SL inside TP level when TP is enabled

# Strategy direction
# inverse: close < range_low -> LONG, close > range_high -> SHORT (current behavior)
# normal: close > range_high -> LONG, close < range_low -> SHORT
STRATEGY_MODE = 'inverse'  # 'normal' or 'inverse'

# Entry mode (giris stili):
# - 'normal'      : su anki davranis, limit giris range kirilan sinirindan olur.
# - 'equilibrium' : breakout gorulunce limit giris range ortasina (midpoint) konur.
# - 'market'      : breakout kapandigi an market emri ile giris yapilir.
ENTRY_MODE = 'market'  # 'normal', 'equilibrium', or 'market'

# SL placement options (strategy tuning):
# - 'distance': current behavior, SL based on breakout candle range * SL_MULTIPLIER
# - 'range'   : SL distance = (range_high - range_low) * SL_MULTIPLIER
SL_PLACEMENT = 'range'  # 'distance' or 'range'
# Legacy setting kept for backward compatibility (not used by current range mode).
SL_BOUNDARY_OFFSET = 0.0

# EMA trade filter (applied at entry/fill moment)
EMA_FILTER_ENABLED = False # True: EMA filtre aktif. False: filtre kapali (EMA kontrol edilmez)
EMA_PERIOD = 50 # LONG: entry_price > EMA, SHORT: entry_price < EMA (kontrol giris/fill aninda yapilir)

# Curve-fit diagnostic (fast, trade-level only)
CURVE_CHECK_ENABLED = True
CURVE_SPLIT_DATE = None # e.g. '2023-01-01' for IS/OOS split; None = auto 80/20 by trades
CURVE_MIN_TRADES = 20
CURVE_PF_MIN = 1.1
CURVE_SHARPE_MIN = 0.0
CURVE_WINRATE_DROP = 15.0  # percentage points
CURVE_START_YEAR = None  # e.g. 2020; None = use all trades

# OOS / forward report (time-based or last % of trades; separate from trade-count split)
OOS_REPORT_ENABLED = True
OOS_START_DATE = None  # e.g. '2025-01-01' (inclusive)
OOS_END_DATE = None    # e.g. '2025-03-31' (inclusive)
OOS_PCT = 0.2         # e.g. 0.2 = last 20% of trades (used only if dates are None)

# Rolling OOS diagnostic (time-based, no shuffle).
# Note: this is not a true re-optimization walk-forward; it scores rolling OOS windows only.
WF_ENABLED = True
WF_TRAIN_PCT = 0.6  # portion of total days for training window
WF_TEST_PCT = 0.2   # portion of total days for each OOS window
WF_STEP_PCT = 0.1   # portion of total days to advance per fold (smaller = more folds)
WF_PF_MIN = 1.05
WF_MIN_FOLDS = 3

# Prop firm evaluation (configure to your firm rules)
PROP_ENABLED = True
PROP_PROFIT_TARGET_PCT = 13.0   # profit target as % of starting balance
PROP_PROFIT_TARGET_ABS = None   # absolute profit target; overrides pct if set
# Max drawdown mode:
# - 'static'  : fixed from STARTING_BALANCE (no trailing)
# - 'trailing': from peak equity
PROP_MAX_DD_MODE = 'static'
PROP_MAX_DD_PCT = 9.0         # max drawdown as % of starting balance
PROP_MAX_DD_ABS = None          # absolute max drawdown; overrides pct if set
# Daily drawdown:
# - set ONE of the values below to enable (leave BOTH None to disable)
# - use _PCT for % of starting balance, or _ABS for fixed currency
# - if both are set, _ABS overrides _PCT
PROP_DAILY_DD_PCT = None        # e.g. 5.0 means 5% of starting balance
PROP_DAILY_DD_ABS = 200.0       # e.g. 200.0 means $200 daily loss limit
# Daily DD mode:
# - 'start': compares against day's starting equity
# - 'peak' : trailing within the day (from day's peak equity)
PROP_DAILY_DD_MODE = 'start'
PROP_MC_ENABLED = True
PROP_MC_ITER = 1000
PROP_MC_SEED = 42
PROP_MC_SHUFFLE_BY = 'day'      # 'trade' or 'day' (day keeps intraday order)
PROP_GRID_ENABLED = True     # avoid heavy grid runs; enable if needed

# Grid search (optional)
GRID_SEARCH_ENABLED = False
GRID_SL_MULTIPLIERS = [1.0,2.0,3.0,4.0]
GRID_RR_RATIOS = [1.0,2.0,3.0,4.0]
GRID_RANGE_WINDOWS = [

     (13, 30, 15, 00)
]
# Grid search now also tests BE/EMA combinations for stronger edge discovery.
GRID_BREAKEVEN_ENABLED_OPTIONS = [True,False] # True or False or True/False combo for quick BE on/off comparison
GRID_BREAKEVEN_TRIGGER_R_OPTIONS = [0.5,1.0]
GRID_BREAKEVEN_OFFSET_OPTIONS = [0.0,0.5,1.0]
GRID_BREAKEVEN_TRIGGER_MODE_OPTIONS = [BREAKEVEN_TRIGGER_MODE]
GRID_BREAKEVEN_ALLOW_SAME_BAR_OPTIONS = [BREAKEVEN_ALLOW_SAME_BAR]
# Grid trailing examples:
# - Fast test: GRID_TRAILING_SL_ENABLED_OPTIONS = [False, True]
# - Wider test: GRID_TRAILING_SL_ACTIVATE_R_OPTIONS = [0.75, 1.0, 1.5]
# - Keep distance below/near RR_RATIO if you want a tighter trailing stop.
GRID_TRAILING_SL_ENABLED_OPTIONS = [False]
GRID_TRAILING_SL_ACTIVATE_R_OPTIONS = [1.0]
GRID_TRAILING_SL_DISTANCE_R_OPTIONS = [0.75]
GRID_TRAILING_SL_STEP_R_OPTIONS = [0.25]
GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS = [False]
GRID_TRAILING_SL_TRIGGER_MODE_OPTIONS = [TRAILING_SL_TRIGGER_MODE]
GRID_TRAILING_SL_CAP_AT_TP_OPTIONS = [TRAILING_SL_CAP_AT_TP]
GRID_EMA_FILTER_OPTIONS = [False] # True or False or True/False combo for quick EMA filter on/off comparison
GRID_EMA_PERIOD_OPTIONS = [200]
# Strategy behavior search options.
# Accepted forms for all options below:
# - string: "range", "distance", "range,distance"
# - string: "normal,inverse"
# - string: "normal,equilibrium,market"
# - list/tuple/set: ['range', 'distance'] / ['normal', 'inverse']
GRID_SL_PLACEMENT_OPTIONS = ['range', 'distance']
GRID_STRATEGY_MODE_OPTIONS = ['normal', 'inverse']
GRID_ENTRY_MODE_OPTIONS = ['market','normal','equilibrium']  # normal: sinir, equilibrium: orta, market: aninda
GRID_METRICS = ['edge_score', 'robustness_score', 'wf_median_pf', 'oos_profit_factor', 'profit_factor', 'sharpe', 'total_pnl']  # higher is better
GRID_TOP_N = 5
GRID_PRINT_STYLE = 'compact'  # 'compact' (one-line rows) or 'table' (wide dataframe)
GRID_MIN_TRADES_FOR_RANK = 100
GRID_MIN_OOS_TRADES_FOR_RANK = 20
GRID_INF_METRIC_CAP = 5.0
GRID_INF_SCORE_FACTOR = 0.90
# Output CSV creation guard.
# Keep this False to prevent any CSV file writes during runs.
CSV_EXPORT_ENABLED = False
GRID_RESULTS_CSV = 'grid_results.csv'
GRID_EDGE_RESULTS_CSV = 'grid_edge_results.csv'
GRID_INCLUDE_DISTRIBUTION = True
GRID_PROGRESS_ENABLED = True
GRID_SPLIT_ENABLED = True
GRID_SPLIT_TRAIN_PCT = 0.7  # 80% IS / 20% OOS by trades
GRID_RANDOM_ENABLED = True
GRID_RANDOM_RANGE_WINDOWS = []
GRID_RANDOM_PF_MIN = 1.05
GRID_MC_ENABLED = True
GRID_MC_ITER = 200
GRID_MC_SEED = 42
GRID_ROBUSTNESS_SHORTLIST_SIZE = 80
GRID_MIN_ROBUSTNESS_SCORE = 80.0  # Applied only when GRID_ROBUSTNESS_ENABLED=True
# Parallel grid search options
GRID_PARALLEL_ENABLED = True
GRID_WORKERS = None  # None = auto-detect (cpu_count()-1)
GRID_USE_DATA_CACHE_FOR_WORKERS = True
# Allow grid to vary pending expiry and trade end time
GRID_PENDING_EXPIRY_BARS_OPTIONS = [5,15]
# list of (hour, minute) tuples
GRID_TRADE_END_OPTIONS = [
    (19, 0)
]
# Solo execution/risk inputs that can also be varied by grid search.
# Defaults are single-value lists so existing grid size does not explode.
GRID_ALLOW_SAME_BAR_FILL_OPTIONS = [ALLOW_SAME_BAR_FILL]
GRID_MAX_TRADES_PER_DAY_OPTIONS = [MAX_TRADES_PER_DAY]
GRID_ONE_SIGNAL_PER_DAY_OPTIONS = [ONE_SIGNAL_PER_DAY]
GRID_PENDING_TOUCH_MODE_OPTIONS = [PENDING_TOUCH_MODE]  # 'wick' or 'close'
GRID_CANCEL_PENDING_ON_OPPOSITE_BREAKOUT_OPTIONS = [CANCEL_PENDING_ON_OPPOSITE_BREAKOUT]
GRID_CLOSE_POSITION_ON_DAY_CHANGE_OPTIONS = [CLOSE_POSITION_ON_DAY_CHANGE]
GRID_INTRABAR_EXIT_POLICY_OPTIONS = [INTRABAR_EXIT_POLICY]
GRID_FILL_BAR_EXIT_POLICY_OPTIONS = [FILL_BAR_EXIT_POLICY]
GRID_RISK_MODE_OPTIONS = [RISK_MODE]  # 'fixed' or 'percent'
GRID_RISK_PERCENT_PER_TRADE_OPTIONS = [RISK_PERCENT_PER_TRADE]
GRID_FIXED_RISK_PER_TRADE_OPTIONS = [FIXED_RISK_PER_TRADE]
GRID_BACKTEST_COST_PER_TRADE_OPTIONS = [BACKTEST_COST_PER_TRADE]
GRID_BACKTEST_COST_POINTS_OPTIONS = [BACKTEST_COST_POINTS]

# Window robustness (random shift stability check)
# Purpose:
# - Test whether selected range window keeps edge after small random minute shifts.
# - If performance collapses with +/- shifts, edge is likely overfit to exact timestamps.
WINDOW_ROBUSTNESS_ENABLED = True
WINDOW_ROBUSTNESS_SHIFTS = 12            # random shift trials per base window
WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT = True
WINDOW_ROBUSTNESS_SHIFT_BARS = 2         # when data-driven: max shift = timeframe_minutes * SHIFT_BARS
WINDOW_ROBUSTNESS_MAX_SHIFT_MINUTES = 10 # manual fallback if data-driven disabled
WINDOW_ROBUSTNESS_STEP_MINUTES = 1       # manual fallback if data-driven disabled
WINDOW_ROBUSTNESS_SEED = 42
WINDOW_ROBUSTNESS_MIN_TRADES = 10        # shifts below this trade count are excluded from score
WINDOW_ROBUSTNESS_PF_PASS = 1.0          # PF threshold for pass ratio in score
WINDOW_ROBUSTNESS_RESULTS_CSV = 'window_robustness_results.csv'
WINDOW_ROBUSTNESS_PRINT_ROWS = 20

# Optional robustness in grid search.
# Single switch behavior:
# - GRID_ROBUSTNESS_ENABLED=False -> no robustness eval, no robustness filtering.
# - GRID_ROBUSTNESS_ENABLED=True  -> run robustness eval; optional threshold filter can apply.
GRID_ROBUSTNESS_ENABLED = True
GRID_ROBUSTNESS_SHIFTS = 5
GRID_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT = True
GRID_ROBUSTNESS_SHIFT_BARS = 2
GRID_ROBUSTNESS_MAX_SHIFT_MINUTES = 10
GRID_ROBUSTNESS_STEP_MINUTES = 1

# Distribution analysis (per-trade, fast)
DISTRIBUTION_ANALYSIS_ENABLED = True
DISTRIBUTION_MODE = 'trade'  # 'trade' (to exit), 'session' (to TRADE_END_TIME), 'bars' (fixed horizon)
DISTRIBUTION_HORIZON_BARS = 15 # only used when DISTRIBUTION_MODE='bars'

# Output settings
SHOW_BALANCE_PLOT = True
SHOW_TRADE_PLOTS = True
TRADE_PLOT_BACKEND = 'lightweight'  # 'lightweight' or 'matplotlib'
TRADE_PLOT_LIGHTWEIGHT_WIDTH = 1600
TRADE_PLOT_LIGHTWEIGHT_HEIGHT = 900
TRADE_PLOT_LIGHTWEIGHT_BLOCKING = True
TRADE_PLOT_TOOLBOX_ENABLED = True   # lightweight chart left toolbox (draw tools)
TRADE_PLOT_MEASURE_TOOL_ENABLED = True  # add "Price Range" helper button + measurement label
TRADE_PLOTS_COUNT = 3
TRADE_PLOTS_WINDOW = 200
# Random trade chart horizontal stretch (TradingView-like zoom on x-axis spacing).
# 1.0 = default spacing, >1 widens candles, <1 compresses candles.
RANDOM_TRADE_X_STRETCH = 1.0
PROGRESS_ENABLED = True
PROGRESS_EVERY_N = 5000  # bars
SHOW_DEBUG_COUNTS = False
SHOW_WEEKDAY_STATS = True
# Weekday filter (optional). Use 3-letter codes: Mon, Tue, Wed, Thu, Fri, Sat, Sun
BLOCKED_WEEKDAYS = []  # e.g. ['Wed'] to skip Wednesday trades

# -----------------------------------------------------------------------------
# NEWS FILTER FLOW (historical backtest integration)
# 1) Read event calendar from NEWS_CALENDAR_FILE (expects UTC timestamps).
# 2) Filter events by NEWS_IMPACTS and NEWS_BLOCK_CURRENCIES.
# 3) Build trigger bars for [event_time - NEWS_PRE_EVENT_MINUTES, event_time).
# 4) At trigger bar, block trading day; optionally force-close current position.
#
# Safety gates added to prevent misleading results:
# - NEWS_DATA_UTC_CONFIRMED: explicit opt-in after timezone verification.
# - NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS: stale-calendar protection vs data_end.
# - NEWS_REQUIRE_EVENTS_IN_DATA_RANGE: optional fail-fast if no overlap.
# -----------------------------------------------------------------------------
# News filter (UTC-only; keep disabled until candle dataset UTC is verified)
NEWS_FILTER_ENABLED = False
NEWS_CALENDAR_FILE = '../Forex Factory Calendar Dataset/Forex Factory Calendar Dataset.csv'
NEWS_PRE_EVENT_MINUTES = 10
NEWS_IMPACTS = ['High', 'High Impact Expected']
NEWS_BLOCK_CURRENCIES = []  # Empty = all currencies
NEWS_FORCE_CLOSE_AND_SKIP_DAY = False
# Safety guards before enabling news filter.
NEWS_DATA_UTC_CONFIRMED = False # Set True only after you verify candle timestamps are UTC.
NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS = 7  # None disables stale-calendar protection.
NEWS_REQUIRE_EVENTS_IN_DATA_RANGE = False # True -> fail if selected news has no overlap with data range.


def _read_startup_config_from_argv():
    # The GUI launches this same file in a new terminal with --config <json>.
    # Reading it before data load lets DATA_FILE and START_DATE take effect.
    args = sys.argv[1:]
    for idx, arg in enumerate(args):
        config_path = None
        if arg == '--config' and idx + 1 < len(args):
            config_path = args[idx + 1]
        elif arg.startswith('--config='):
            config_path = arg.split('=', 1)[1]
        if not config_path:
            continue
        with open(config_path, 'r', encoding='utf-8-sig') as config_file:
            loaded = json.load(config_file)
        if not isinstance(loaded, dict):
            raise ValueError("--config JSON must contain an object/dict.")
        return loaded
    return {}


def _apply_startup_config(config):
    # Only ALL_CAPS settings are accepted; unknown keys are ignored for forward compatibility.
    for name, value in (config or {}).items():
        if isinstance(name, str) and name.isupper() and name in globals():
            globals()[name] = value


_STARTUP_CONFIG = _read_startup_config_from_argv()
_apply_startup_config(_STARTUP_CONFIG)

# Internal caches (speed-up for grid search)
_DATA_CACHE = {}
_NEWS_PLAN_CACHE = {}

RANGE_START_TIME = pd.Timestamp(f'{RANGE_START_HOUR:02d}:{RANGE_START_MINUTE:02d}:00').time()
RANGE_END_TIME = pd.Timestamp(f'{RANGE_END_HOUR:02d}:{RANGE_END_MINUTE:02d}:00').time()
TRADE_END_TIME = pd.Timestamp(f'{TRADE_END_HOUR:02d}:{TRADE_END_MINUTE:02d}:00').time()

# -----------------------------------------------------------------------------
# Data load and prep
# -----------------------------------------------------------------------------
def resolve_data_file_path(path_text):
    raw = Path(path_text).expanduser()
    if raw.is_absolute():
        return raw.resolve()

    script_dir = Path(__file__).resolve().parent
    candidates = [script_dir / raw, Path.cwd() / raw, script_dir.parent / raw]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (script_dir / raw).resolve()


def detect_csv_separator(file_path, delimiters=',;\t|', sample_size=8192, file_label='CSV'):
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        sample = f.read(sample_size)

    if not sample.strip():
        raise ValueError(f"{file_label} is empty: {file_path}")

    try:
        return csv.Sniffer().sniff(sample, delimiters=delimiters).delimiter
    except csv.Error:
        lines = [line for line in sample.splitlines() if line.strip()][:5]
        best_sep = None
        best_score = None
        for sep in delimiters:
            counts = [line.count(sep) for line in lines]
            non_zero = [count for count in counts if count > 0]
            if not non_zero:
                continue
            # Prefer delimiters that appear on more lines and with stable column splits.
            score = (len(non_zero), -(max(non_zero) - min(non_zero)), sum(non_zero))
            if best_score is None or score > best_score:
                best_score = score
                best_sep = sep
        if best_sep is None:
            raise ValueError(
                f"Could not auto-detect separator for {file_label}: {file_path}. "
                "Expected one of: ',', ';', '\\t', '|'."
            )
        return best_sep


resolved_data_file = resolve_data_file_path(DATA_FILE)
if not resolved_data_file.exists():
    raise FileNotFoundError(
        f"DATA_FILE not found: {DATA_FILE} -> {resolved_data_file} "
        f"(cwd={Path.cwd()})"
    )

detected_sep = detect_csv_separator(resolved_data_file, file_label='DATA_FILE')
df = pd.read_csv(resolved_data_file, sep=detected_sep)
df = df.drop_duplicates()
df = df.dropna()
df.columns = df.columns.astype(str).str.strip()

# Normalize both supported export schemas:
# 1) DateTime, Open, High, Low, Close, ...
# 2) time, open, high, low, close, ...
# 3) <DATE>, <TIME>, <OPEN>, <HIGH>, <LOW>, <CLOSE>, ...
if {'<DATE>', '<TIME>'}.issubset(df.columns):
    df['date'] = df['<DATE>'].astype(str).str.strip() + ' ' + df['<TIME>'].astype(str).str.strip()
elif 'DateTime' in df.columns:
    df['date'] = df['DateTime'].astype(str).str.strip()
elif 'time' in df.columns:
    df['date'] = df['time'].astype(str).str.strip()
else:
    raise ValueError("Unsupported data format: expected time, DateTime, or <DATE>/<TIME> columns.")

df = df.rename(
    columns={
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        '<OPEN>': 'open',
        '<HIGH>': 'high',
        '<LOW>': 'low',
        '<CLOSE>': 'close',
    }
)

df = df.drop(
    columns=[
        'DateTime',
        'time',
        '<DATE>',
        '<TIME>',
        'Volume',
        'TickVolume',
        'tick_volume',
        '<VOL>',
        '<TICKVOL>',
        '<SPREAD>',
        'spread',
        'real_volume',
    ],
    errors='ignore',
)

date_raw = df['date'].astype(str).str.strip()
df['date'] = pd.to_datetime(date_raw, format='%Y.%m.%d %H:%M:%S', errors='coerce')
if df['date'].isna().all():
    df['date'] = pd.to_datetime(date_raw, format='mixed', errors='coerce')

for col in ['open', 'high', 'low', 'close']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df = df.dropna(subset=['date', 'open', 'high', 'low', 'close'])
df = df.sort_values(by='date', ascending=True)
df = df.drop_duplicates(subset='date', keep='last')
if START_DATE:
    df = df[df['date'] >= pd.to_datetime(START_DATE)]
df.set_index('date', inplace=True)
df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()

# Helper columns
df['day'] = df.index.normalize()
df['time'] = df.index.time


def time_mask(series, start_time, end_time, include_end=True):
    if start_time <= end_time:
        if include_end:
            return (series >= start_time) & (series <= end_time)
        return (series >= start_time) & (series < end_time)
    if include_end:
        return (series >= start_time) | (series <= end_time)
    return (series >= start_time) | (series < end_time)


def in_time_window(value, start_time, end_time, include_end=True):
    if start_time <= end_time:
        if include_end:
            return start_time <= value <= end_time
        return start_time <= value < end_time
    if include_end:
        return value >= start_time or value <= end_time
    return value >= start_time or value < end_time


def _normalize_grid_choice_options(raw_value, option_name, allowed_values):
    # Supports comma/slash-separated string and list/tuple/set mixtures.
    if isinstance(raw_value, str):
        tokens = [part.strip() for part in re.split(r"[,/]", raw_value) if part.strip()]
    elif isinstance(raw_value, (list, tuple, set)):
        tokens = []
        for item in raw_value:
            if isinstance(item, str):
                tokens.extend([part.strip() for part in re.split(r"[,/]", item) if part.strip()])
            else:
                tokens.append(str(item))
    else:
        raise ValueError(f"{option_name} must be string or list/tuple/set")

    if not tokens:
        raise ValueError(f"{option_name} cannot be empty")

    allowed_map = {str(v).lower(): str(v) for v in allowed_values}

    normalized = []
    for token in tokens:
        key = str(token).strip().lower()
        if key not in allowed_map:
            allowed_txt = ", ".join([str(v) for v in allowed_values])
            raise ValueError(
                f"{option_name} has invalid value '{token}'. "
                f"Allowed: {allowed_txt}."
            )
        normalized.append(allowed_map[key])

    unique = []
    seen = set()
    for value in normalized:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def resolve_grid_sl_placement_options():
    return _normalize_grid_choice_options(
        raw_value=GRID_SL_PLACEMENT_OPTIONS,
        option_name='GRID_SL_PLACEMENT_OPTIONS',
        allowed_values=['distance', 'range'],
    )


def resolve_grid_strategy_mode_options():
    return _normalize_grid_choice_options(
        raw_value=GRID_STRATEGY_MODE_OPTIONS,
        option_name='GRID_STRATEGY_MODE_OPTIONS',
        allowed_values=['normal', 'inverse'],
    )


def resolve_grid_entry_mode_options():
    return _normalize_grid_choice_options(
        raw_value=GRID_ENTRY_MODE_OPTIONS,
        option_name='GRID_ENTRY_MODE_OPTIONS',
        allowed_values=['normal', 'equilibrium', 'market'],
    )


def is_valid_breakeven_settings(enabled, trigger_r, offset_r):
    if not enabled or not BREAKEVEN_REQUIRE_OFFSET_BELOW_TRIGGER:
        return True
    trigger_r = float(trigger_r)
    offset_r = float(offset_r)
    if trigger_r <= 0:
        return offset_r == 0
    return offset_r < trigger_r


def _better_stop(direction, new_stop, current_stop):
    if direction == 'LONG':
        return float(new_stop) > float(current_stop)
    return float(new_stop) < float(current_stop)


def _stop_improvement_enough(direction, new_stop, current_stop, min_step):
    if direction == 'LONG':
        return (float(new_stop) - float(current_stop)) >= float(min_step)
    return (float(current_stop) - float(new_stop)) >= float(min_step)


def _as_option_list(value):
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _validate_grid_options(name, allowed_values=None, value_type=None, min_value=None, allow_none=False):
    options = _as_option_list(globals().get(name))
    if not options:
        raise ValueError(f"{name} cannot be empty")
    for raw in options:
        if raw is None and allow_none:
            continue
        if value_type == 'bool' and not isinstance(raw, bool):
            raise ValueError(f"{name} values must be True or False")
        if value_type == 'int' and int(raw) != float(raw):
            raise ValueError(f"{name} values must be integers")
        if value_type in {'int', 'float'}:
            number = float(raw)
            if min_value is not None and number < float(min_value):
                raise ValueError(f"{name} values must be >= {min_value}")
        if allowed_values is not None and str(raw) not in allowed_values:
            allowed = ", ".join(sorted(allowed_values))
            raise ValueError(f"{name} has invalid value '{raw}'. Allowed: {allowed}")


def validate_config():
    if not isinstance(RISK_SETTINGS, dict):
        raise ValueError("RISK_SETTINGS must be a dict")
    if not isinstance(RISK_SETTINGS.get('enabled'), bool):
        raise ValueError("RISK_SETTINGS['enabled'] must be True or False")
    risk_mode = str(RISK_SETTINGS.get('mode', '')).lower()
    if risk_mode not in {'martingale', 'anti_martingale', 'anti_martingale_ladder'}:
        raise ValueError("RISK_SETTINGS['mode'] must be 'martingale', 'anti_martingale', or 'anti_martingale_ladder'")
    if float(RISK_SETTINGS.get('min_risk', 0.0)) <= 0:
        raise ValueError("RISK_SETTINGS['min_risk'] must be > 0")
    if float(RISK_SETTINGS.get('max_risk', 0.0)) < float(RISK_SETTINGS.get('min_risk', 0.0)):
        raise ValueError("RISK_SETTINGS['max_risk'] must be >= min_risk")
    if float(RISK_SETTINGS.get('multiplier', 1.0)) < 1.0:
        raise ValueError("RISK_SETTINGS['multiplier'] must be >= 1.0")
    if float(RISK_SETTINGS.get('win_step', 0.0)) < 0:
        raise ValueError("RISK_SETTINGS['win_step'] must be >= 0")
    loss_multiplier = float(RISK_SETTINGS.get('loss_multiplier', 1.0))
    if loss_multiplier <= 0 or loss_multiplier > 1:
        raise ValueError("RISK_SETTINGS['loss_multiplier'] must be > 0 and <= 1")
    if float(RISK_SETTINGS.get('prop_safety_buffer_pct', 0.0)) < 0 or float(RISK_SETTINGS.get('prop_safety_buffer_pct', 0.0)) >= 100:
        raise ValueError("RISK_SETTINGS['prop_safety_buffer_pct'] must be >= 0 and < 100")
    if not isinstance(EQUITY_IS_OOS_ENABLED, bool):
        raise ValueError("EQUITY_IS_OOS_ENABLED must be True or False")
    if EQUITY_IS_OOS_TRAIN_PCT <= 0 or EQUITY_IS_OOS_TRAIN_PCT >= 1:
        raise ValueError("EQUITY_IS_OOS_TRAIN_PCT must be between 0 and 1")
    if EQUITY_IS_OOS_SHADE_ALPHA < 0 or EQUITY_IS_OOS_SHADE_ALPHA > 1:
        raise ValueError("EQUITY_IS_OOS_SHADE_ALPHA must be between 0 and 1")
    if EDGE_GUARD_MIN_TRADES_PER_SEGMENT < 1 or EDGE_GUARD_MIN_OOS_PF <= 0 or EDGE_GUARD_MAX_OOS_DD_PCT <= 0:
        raise ValueError("EDGE_GUARD thresholds must be positive")
    if RANGE_START_TIME > RANGE_END_TIME:
        raise ValueError("RANGE window cannot cross midnight with current day-based logic.")
    if RANGE_END_TIME >= TRADE_END_TIME:
        raise ValueError("TRADE_END_TIME must be > RANGE_END_TIME.")
    if RANGE_START_TIME == RANGE_END_TIME:
        raise ValueError("RANGE window must have non-zero length.")
    if BAR_TIME_MODE not in {'open', 'close'}:
        raise ValueError("BAR_TIME_MODE must be 'open' or 'close'")
    if STARTING_BALANCE <= 0:
        raise ValueError("STARTING_BALANCE must be > 0")
    if PENDING_TOUCH_MODE not in {'wick', 'close'}:
        raise ValueError("PENDING_TOUCH_MODE must be 'wick' or 'close'")
    if INTRABAR_EXIT_POLICY not in {'worst_case', 'best_case', 'tp_first', 'sl_first'}:
        raise ValueError("INTRABAR_EXIT_POLICY must be 'worst_case', 'best_case', 'tp_first', or 'sl_first'")
    if FILL_BAR_EXIT_POLICY not in {'conservative', 'ohlc'}:
        raise ValueError("FILL_BAR_EXIT_POLICY must be 'conservative' or 'ohlc'")
    if BACKTEST_COST_PER_TRADE < 0:
        raise ValueError("BACKTEST_COST_PER_TRADE must be >= 0")
    if BACKTEST_COST_POINTS < 0:
        raise ValueError("BACKTEST_COST_POINTS must be >= 0")
    if not isinstance(EXECUTION_REALISM_REQUIRED, bool):
        raise ValueError("EXECUTION_REALISM_REQUIRED must be True or False")
    if BREAKEVEN_TRIGGER_MODE not in {'wick', 'close'}:
        raise ValueError("BREAKEVEN_TRIGGER_MODE must be 'wick' or 'close'")
    if BREAKEVEN_TRIGGER_R < 0:
        raise ValueError("BREAKEVEN_TRIGGER_R must be >= 0")
    if BREAKEVEN_OFFSET < 0:
        raise ValueError("BREAKEVEN_OFFSET must be >= 0")
    if not is_valid_breakeven_settings(BREAKEVEN_ENABLED, BREAKEVEN_TRIGGER_R, BREAKEVEN_OFFSET):
        raise ValueError(
            "BREAKEVEN_OFFSET must be lower than BREAKEVEN_TRIGGER_R when "
            "BREAKEVEN_REQUIRE_OFFSET_BELOW_TRIGGER=True. Example: trigger=0.5, offset=0.0."
        )
    if TRAILING_SL_TRIGGER_MODE not in {'wick', 'close'}:
        raise ValueError("TRAILING_SL_TRIGGER_MODE must be 'wick' or 'close'")
    if TRAILING_SL_ACTIVATE_R < 0:
        raise ValueError("TRAILING_SL_ACTIVATE_R must be >= 0")
    if TRAILING_SL_DISTANCE_R <= 0:
        raise ValueError("TRAILING_SL_DISTANCE_R must be > 0")
    if TRAILING_SL_STEP_R < 0:
        raise ValueError("TRAILING_SL_STEP_R must be >= 0")
    if not isinstance(TRAILING_SL_LOCK_BREAKEVEN, bool):
        raise ValueError("TRAILING_SL_LOCK_BREAKEVEN must be True or False")
    if not isinstance(TRAILING_SL_CAP_AT_TP, bool):
        raise ValueError("TRAILING_SL_CAP_AT_TP must be True or False")
    if PENDING_EXPIRY_BARS < 1:
        raise ValueError("PENDING_EXPIRY_BARS must be >= 1")
    if FIXED_RISK_PER_TRADE <= 0:
        raise ValueError("FIXED_RISK_PER_TRADE must be > 0")
    if RISK_MODE not in {'fixed', 'percent'}:
        raise ValueError("RISK_MODE must be 'fixed' or 'percent'")
    if RISK_MODE == 'percent' and RISK_PERCENT_PER_TRADE <= 0:
        raise ValueError("RISK_PERCENT_PER_TRADE must be > 0")
    if PRICE_TOUCH_TOLERANCE < 0:
        raise ValueError("PRICE_TOUCH_TOLERANCE must be >= 0")
    if STRATEGY_MODE not in {'normal', 'inverse'}:
        raise ValueError("STRATEGY_MODE must be 'normal' or 'inverse'")
    if ENTRY_MODE not in {'normal', 'equilibrium', 'market'}:
        raise ValueError("ENTRY_MODE must be 'normal', 'equilibrium', or 'market'")
    if SL_PLACEMENT not in {'distance', 'range'}:
        raise ValueError("SL_PLACEMENT must be 'distance' or 'range'")
    if SL_BOUNDARY_OFFSET < 0:
        raise ValueError("SL_BOUNDARY_OFFSET must be >= 0")
    if str(TRADE_PLOT_BACKEND).strip().lower() not in {'lightweight', 'matplotlib'}:
        raise ValueError("TRADE_PLOT_BACKEND must be 'lightweight' or 'matplotlib'")
    if TRADE_PLOT_LIGHTWEIGHT_WIDTH <= 0 or TRADE_PLOT_LIGHTWEIGHT_HEIGHT <= 0:
        raise ValueError("TRADE_PLOT_LIGHTWEIGHT_WIDTH/HEIGHT must be > 0")
    if not isinstance(TRADE_PLOT_LIGHTWEIGHT_BLOCKING, bool):
        raise ValueError("TRADE_PLOT_LIGHTWEIGHT_BLOCKING must be True or False")
    if not isinstance(TRADE_PLOT_TOOLBOX_ENABLED, bool):
        raise ValueError("TRADE_PLOT_TOOLBOX_ENABLED must be True or False")
    if not isinstance(TRADE_PLOT_MEASURE_TOOL_ENABLED, bool):
        raise ValueError("TRADE_PLOT_MEASURE_TOOL_ENABLED must be True or False")
    if SL_MULTIPLIER <= 0:
        raise ValueError("SL_MULTIPLIER must be > 0")
    if EMA_PERIOD < 1:
        raise ValueError("EMA_PERIOD must be >= 1")
    if CURVE_MIN_TRADES < 5:
        raise ValueError("CURVE_MIN_TRADES must be >= 5")
    if CURVE_PF_MIN <= 0:
        raise ValueError("CURVE_PF_MIN must be > 0")
    if CURVE_WINRATE_DROP < 0:
        raise ValueError("CURVE_WINRATE_DROP must be >= 0")
    if CURVE_START_YEAR is not None:
        if not isinstance(CURVE_START_YEAR, int):
            raise ValueError("CURVE_START_YEAR must be an int or None")
    if OOS_REPORT_ENABLED:
        start_ts = pd.to_datetime(OOS_START_DATE) if OOS_START_DATE else None
        end_ts = pd.to_datetime(OOS_END_DATE) if OOS_END_DATE else None
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ValueError("OOS_START_DATE must be <= OOS_END_DATE")
        if OOS_PCT is not None:
            if OOS_PCT <= 0 or OOS_PCT >= 1:
                raise ValueError("OOS_PCT must be between 0 and 1 (e.g. 0.2)")
    if WF_ENABLED:
        if WF_TRAIN_PCT <= 0 or WF_TRAIN_PCT >= 1:
            raise ValueError("WF_TRAIN_PCT must be between 0 and 1 (e.g. 0.6)")
        if WF_TEST_PCT <= 0 or WF_TEST_PCT >= 1:
            raise ValueError("WF_TEST_PCT must be between 0 and 1 (e.g. 0.2)")
        if WF_STEP_PCT <= 0 or WF_STEP_PCT >= 1:
            raise ValueError("WF_STEP_PCT must be between 0 and 1 (e.g. 0.2)")
        if (WF_TRAIN_PCT + WF_TEST_PCT) >= 1:
            raise ValueError("WF_TRAIN_PCT + WF_TEST_PCT must be < 1 for walk-forward")
        if WF_PF_MIN <= 0:
            raise ValueError("WF_PF_MIN must be > 0")
        if WF_MIN_FOLDS < 1:
            raise ValueError("WF_MIN_FOLDS must be >= 1")
    if PROP_ENABLED:
        if PROP_PROFIT_TARGET_ABS is None and (PROP_PROFIT_TARGET_PCT is None or PROP_PROFIT_TARGET_PCT <= 0):
            raise ValueError("Set PROP_PROFIT_TARGET_PCT > 0 or PROP_PROFIT_TARGET_ABS")
        if PROP_MAX_DD_ABS is None and (PROP_MAX_DD_PCT is None or PROP_MAX_DD_PCT <= 0):
            raise ValueError("Set PROP_MAX_DD_PCT > 0 or PROP_MAX_DD_ABS")
        if PROP_MAX_DD_MODE not in {'static', 'trailing'}:
            raise ValueError("PROP_MAX_DD_MODE must be 'static' or 'trailing'")
        if PROP_DAILY_DD_MODE not in {'start', 'peak'}:
            raise ValueError("PROP_DAILY_DD_MODE must be 'start' or 'peak'")
        if PROP_MC_ITER < 1:
            raise ValueError("PROP_MC_ITER must be >= 1")
        if PROP_MC_SHUFFLE_BY not in {'trade', 'day'}:
            raise ValueError("PROP_MC_SHUFFLE_BY must be 'trade' or 'day'")
    if WINDOW_ROBUSTNESS_ENABLED:
        if WINDOW_ROBUSTNESS_SHIFTS < 1:
            raise ValueError("WINDOW_ROBUSTNESS_SHIFTS must be >= 1")
        if not isinstance(WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT, bool):
            raise ValueError("WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT must be True or False")
        if WINDOW_ROBUSTNESS_SHIFT_BARS < 1:
            raise ValueError("WINDOW_ROBUSTNESS_SHIFT_BARS must be >= 1")
        if WINDOW_ROBUSTNESS_MAX_SHIFT_MINUTES < 1:
            raise ValueError("WINDOW_ROBUSTNESS_MAX_SHIFT_MINUTES must be >= 1")
        if WINDOW_ROBUSTNESS_STEP_MINUTES < 1:
            raise ValueError("WINDOW_ROBUSTNESS_STEP_MINUTES must be >= 1")
        if WINDOW_ROBUSTNESS_MIN_TRADES < 0:
            raise ValueError("WINDOW_ROBUSTNESS_MIN_TRADES must be >= 0")
        if WINDOW_ROBUSTNESS_PF_PASS <= 0:
            raise ValueError("WINDOW_ROBUSTNESS_PF_PASS must be > 0")

    if GRID_SEARCH_ENABLED:
        if GRID_SPLIT_ENABLED:
            if GRID_SPLIT_TRAIN_PCT <= 0 or GRID_SPLIT_TRAIN_PCT >= 1:
                raise ValueError("GRID_SPLIT_TRAIN_PCT must be between 0 and 1 (e.g. 0.8)")
        if not isinstance(GRID_BREAKEVEN_ENABLED_OPTIONS, (list, tuple)) or len(GRID_BREAKEVEN_ENABLED_OPTIONS) == 0:
            raise ValueError("GRID_BREAKEVEN_ENABLED_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_BREAKEVEN_TRIGGER_R_OPTIONS, (list, tuple)) or len(GRID_BREAKEVEN_TRIGGER_R_OPTIONS) == 0:
            raise ValueError("GRID_BREAKEVEN_TRIGGER_R_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_BREAKEVEN_OFFSET_OPTIONS, (list, tuple)) or len(GRID_BREAKEVEN_OFFSET_OPTIONS) == 0:
            raise ValueError("GRID_BREAKEVEN_OFFSET_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_TRAILING_SL_ENABLED_OPTIONS, (list, tuple)) or len(GRID_TRAILING_SL_ENABLED_OPTIONS) == 0:
            raise ValueError("GRID_TRAILING_SL_ENABLED_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_TRAILING_SL_ACTIVATE_R_OPTIONS, (list, tuple)) or len(GRID_TRAILING_SL_ACTIVATE_R_OPTIONS) == 0:
            raise ValueError("GRID_TRAILING_SL_ACTIVATE_R_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_TRAILING_SL_DISTANCE_R_OPTIONS, (list, tuple)) or len(GRID_TRAILING_SL_DISTANCE_R_OPTIONS) == 0:
            raise ValueError("GRID_TRAILING_SL_DISTANCE_R_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_TRAILING_SL_STEP_R_OPTIONS, (list, tuple)) or len(GRID_TRAILING_SL_STEP_R_OPTIONS) == 0:
            raise ValueError("GRID_TRAILING_SL_STEP_R_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS, (list, tuple)) or len(GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS) == 0:
            raise ValueError("GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS must be a non-empty list/tuple")
        for option_name in [
            'GRID_BREAKEVEN_ALLOW_SAME_BAR_OPTIONS',
            'GRID_TRAILING_SL_CAP_AT_TP_OPTIONS',
            'GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS',
            'GRID_ALLOW_SAME_BAR_FILL_OPTIONS',
            'GRID_ONE_SIGNAL_PER_DAY_OPTIONS',
            'GRID_CANCEL_PENDING_ON_OPPOSITE_BREAKOUT_OPTIONS',
            'GRID_CLOSE_POSITION_ON_DAY_CHANGE_OPTIONS',
        ]:
            _validate_grid_options(option_name, value_type='bool')
        _validate_grid_options('GRID_BREAKEVEN_TRIGGER_MODE_OPTIONS', allowed_values={'wick', 'close'})
        _validate_grid_options('GRID_TRAILING_SL_TRIGGER_MODE_OPTIONS', allowed_values={'wick', 'close'})
        _validate_grid_options('GRID_PENDING_TOUCH_MODE_OPTIONS', allowed_values={'wick', 'close'})
        _validate_grid_options('GRID_INTRABAR_EXIT_POLICY_OPTIONS', allowed_values={'worst_case', 'best_case', 'tp_first', 'sl_first'})
        _validate_grid_options('GRID_FILL_BAR_EXIT_POLICY_OPTIONS', allowed_values={'conservative', 'ohlc'})
        _validate_grid_options('GRID_RISK_MODE_OPTIONS', allowed_values={'fixed', 'percent'})
        _validate_grid_options('GRID_MAX_TRADES_PER_DAY_OPTIONS', value_type='int', min_value=1)
        _validate_grid_options('GRID_RISK_PERCENT_PER_TRADE_OPTIONS', value_type='float', min_value=0)
        _validate_grid_options('GRID_FIXED_RISK_PER_TRADE_OPTIONS', value_type='float', min_value=0)
        _validate_grid_options('GRID_BACKTEST_COST_PER_TRADE_OPTIONS', value_type='float', min_value=0)
        _validate_grid_options('GRID_BACKTEST_COST_POINTS_OPTIONS', value_type='float', min_value=0)
        if not isinstance(GRID_EMA_FILTER_OPTIONS, (list, tuple)) or len(GRID_EMA_FILTER_OPTIONS) == 0:
            raise ValueError("GRID_EMA_FILTER_OPTIONS must be a non-empty list/tuple")
        if not isinstance(GRID_EMA_PERIOD_OPTIONS, (list, tuple)) or len(GRID_EMA_PERIOD_OPTIONS) == 0:
            raise ValueError("GRID_EMA_PERIOD_OPTIONS must be a non-empty list/tuple")
        resolve_grid_sl_placement_options()
        resolve_grid_strategy_mode_options()
        resolve_grid_entry_mode_options()
        if not isinstance(GRID_SL_MULTIPLIERS, (list, tuple)) or len(GRID_SL_MULTIPLIERS) == 0:
            raise ValueError("GRID_SL_MULTIPLIERS must be a non-empty list/tuple")
        if not isinstance(GRID_RR_RATIOS, (list, tuple)) or len(GRID_RR_RATIOS) == 0:
            raise ValueError("GRID_RR_RATIOS must be a non-empty list/tuple")
        if any(float(x) <= 0 for x in GRID_SL_MULTIPLIERS):
            raise ValueError("GRID_SL_MULTIPLIERS values must be > 0")
        if any(float(x) <= 0 for x in GRID_RR_RATIOS):
            raise ValueError("GRID_RR_RATIOS values must be > 0")
        if not isinstance(GRID_RANGE_WINDOWS, (list, tuple)) or len(GRID_RANGE_WINDOWS) == 0:
            raise ValueError("GRID_RANGE_WINDOWS must be a non-empty list/tuple")
        for window in GRID_RANGE_WINDOWS:
            if not isinstance(window, (list, tuple)) or len(window) != 4:
                raise ValueError("Each GRID_RANGE_WINDOWS item must be (start_h, start_m, end_h, end_m)")
            ws_h, ws_m, we_h, we_m = [int(x) for x in window]
            if not (0 <= ws_h <= 23 and 0 <= we_h <= 23 and 0 <= ws_m <= 59 and 0 <= we_m <= 59):
                raise ValueError("GRID_RANGE_WINDOWS times must be valid HH:MM values")
            if pd.Timestamp(f"{ws_h:02d}:{ws_m:02d}:00").time() >= pd.Timestamp(f"{we_h:02d}:{we_m:02d}:00").time():
                raise ValueError("GRID_RANGE_WINDOWS cannot cross midnight and must have non-zero length")
        if any(float(x) < 0 for x in GRID_BREAKEVEN_TRIGGER_R_OPTIONS):
            raise ValueError("GRID_BREAKEVEN_TRIGGER_R_OPTIONS values must be >= 0")
        if any(float(x) < 0 for x in GRID_BREAKEVEN_OFFSET_OPTIONS):
            raise ValueError("GRID_BREAKEVEN_OFFSET_OPTIONS values must be >= 0")
        if any(float(x) < 0 for x in GRID_TRAILING_SL_ACTIVATE_R_OPTIONS):
            raise ValueError("GRID_TRAILING_SL_ACTIVATE_R_OPTIONS values must be >= 0")
        if any(float(x) <= 0 for x in GRID_TRAILING_SL_DISTANCE_R_OPTIONS):
            raise ValueError("GRID_TRAILING_SL_DISTANCE_R_OPTIONS values must be > 0")
        if any(float(x) < 0 for x in GRID_TRAILING_SL_STEP_R_OPTIONS):
            raise ValueError("GRID_TRAILING_SL_STEP_R_OPTIONS values must be >= 0")
        if any(int(x) < 1 for x in GRID_EMA_PERIOD_OPTIONS):
            raise ValueError("GRID_EMA_PERIOD_OPTIONS values must be >= 1")
        if GRID_ROBUSTNESS_SHORTLIST_SIZE < 1:
            raise ValueError("GRID_ROBUSTNESS_SHORTLIST_SIZE must be >= 1")
        if GRID_MIN_ROBUSTNESS_SCORE is not None and (GRID_MIN_ROBUSTNESS_SCORE < 0 or GRID_MIN_ROBUSTNESS_SCORE > 100):
            raise ValueError("GRID_MIN_ROBUSTNESS_SCORE must be between 0 and 100, or None")
        if GRID_ROBUSTNESS_ENABLED:
            if GRID_ROBUSTNESS_SHIFTS < 1:
                raise ValueError("GRID_ROBUSTNESS_SHIFTS must be >= 1")
            if not isinstance(GRID_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT, bool):
                raise ValueError("GRID_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT must be True or False")
            if GRID_ROBUSTNESS_SHIFT_BARS < 1:
                raise ValueError("GRID_ROBUSTNESS_SHIFT_BARS must be >= 1")
            if GRID_ROBUSTNESS_MAX_SHIFT_MINUTES < 1:
                raise ValueError("GRID_ROBUSTNESS_MAX_SHIFT_MINUTES must be >= 1")
            if GRID_ROBUSTNESS_STEP_MINUTES < 1:
                raise ValueError("GRID_ROBUSTNESS_STEP_MINUTES must be >= 1")
        if GRID_PRINT_STYLE not in {'compact', 'table'}:
            raise ValueError("GRID_PRINT_STYLE must be 'compact' or 'table'")
        if GRID_TOP_N < 1:
            raise ValueError("GRID_TOP_N must be >= 1")
        if GRID_MIN_TRADES_FOR_RANK < 1:
            raise ValueError("GRID_MIN_TRADES_FOR_RANK must be >= 1")
        if GRID_MIN_OOS_TRADES_FOR_RANK < 1:
            raise ValueError("GRID_MIN_OOS_TRADES_FOR_RANK must be >= 1")
        if GRID_INF_METRIC_CAP <= 0:
            raise ValueError("GRID_INF_METRIC_CAP must be > 0")
        if GRID_INF_SCORE_FACTOR <= 0 or GRID_INF_SCORE_FACTOR > 1:
            raise ValueError("GRID_INF_SCORE_FACTOR must be > 0 and <= 1")
        pending_options = GRID_PENDING_EXPIRY_BARS_OPTIONS if isinstance(GRID_PENDING_EXPIRY_BARS_OPTIONS, (list, tuple, set)) else [GRID_PENDING_EXPIRY_BARS_OPTIONS]
        if not pending_options:
            raise ValueError("GRID_PENDING_EXPIRY_BARS_OPTIONS cannot be empty")
        if any(int(x) < 1 for x in pending_options):
            raise ValueError("GRID_PENDING_EXPIRY_BARS_OPTIONS values must be >= 1")
        if (
            isinstance(GRID_TRADE_END_OPTIONS, tuple)
            and len(GRID_TRADE_END_OPTIONS) == 2
            and not isinstance(GRID_TRADE_END_OPTIONS[0], (list, tuple, set))
        ):
            trade_end_options = [GRID_TRADE_END_OPTIONS]
        else:
            trade_end_options = GRID_TRADE_END_OPTIONS if isinstance(GRID_TRADE_END_OPTIONS, (list, tuple, set)) else [GRID_TRADE_END_OPTIONS]
        if not trade_end_options:
            raise ValueError("GRID_TRADE_END_OPTIONS cannot be empty")
        for trade_end in trade_end_options:
            if not isinstance(trade_end, (list, tuple)) or len(trade_end) != 2:
                raise ValueError("Each GRID_TRADE_END_OPTIONS item must be (hour, minute)")
            th, tm = [int(x) for x in trade_end]
            if not (0 <= th <= 23 and 0 <= tm <= 59):
                raise ValueError("GRID_TRADE_END_OPTIONS values must be valid HH:MM values")
        if GRID_WORKERS is not None and int(GRID_WORKERS) < 1:
            raise ValueError("GRID_WORKERS must be None or >= 1")

    if DISTRIBUTION_ANALYSIS_ENABLED:
        if DISTRIBUTION_MODE not in {'trade', 'session', 'bars'}:
            raise ValueError("DISTRIBUTION_MODE must be 'trade', 'session', or 'bars'")
        if DISTRIBUTION_HORIZON_BARS < 1:
            raise ValueError("DISTRIBUTION_HORIZON_BARS must be >= 1")
    if not isinstance(BLOCKED_WEEKDAYS, (list, tuple, set)):
        raise ValueError("BLOCKED_WEEKDAYS must be a list/tuple/set of weekday codes")
    valid_days = {'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'}
    if any(day not in valid_days for day in BLOCKED_WEEKDAYS):
        raise ValueError("BLOCKED_WEEKDAYS contains invalid codes. Use Mon/Tue/Wed/Thu/Fri/Sat/Sun")
    # News safety guards are relevant only when filter is enabled.
    if NEWS_FILTER_ENABLED:
        if NEWS_PRE_EVENT_MINUTES < 1:
            raise ValueError("NEWS_PRE_EVENT_MINUTES must be >= 1")
        if not isinstance(NEWS_IMPACTS, (list, tuple, set)) or len(NEWS_IMPACTS) == 0:
            raise ValueError("NEWS_IMPACTS must be a non-empty list/tuple/set")
        if not isinstance(NEWS_BLOCK_CURRENCIES, (list, tuple, set)):
            raise ValueError("NEWS_BLOCK_CURRENCIES must be a list/tuple/set")
        if not isinstance(NEWS_DATA_UTC_CONFIRMED, bool):
            raise ValueError("NEWS_DATA_UTC_CONFIRMED must be True or False")
        if NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS is not None and NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS < 0:
            raise ValueError("NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS must be >= 0 or None")
        if not isinstance(NEWS_REQUIRE_EVENTS_IN_DATA_RANGE, bool):
            raise ValueError("NEWS_REQUIRE_EVENTS_IN_DATA_RANGE must be True or False")
        if not NEWS_DATA_UTC_CONFIRMED:
            raise ValueError(
                "NEWS_FILTER_ENABLED=True requires NEWS_DATA_UTC_CONFIRMED=True. "
                "Enable this only after verifying your candle DateTime is UTC."
            )


def _resolve_news_calendar_path(path_text):
    raw = Path(path_text).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        script_dir = Path(__file__).resolve().parent
        candidates.extend([script_dir / raw, script_dir.parent / raw])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    return (Path.cwd() / raw).resolve() if not raw.is_absolute() else raw


def build_news_filter_plan(data_index):
    # Plan output is consumed by run_backtest loop.
    # 'trigger_map' maps bar index -> set(days) that should be blocked due to news.
    plan = {
        'enabled': False,
        'trigger_map': {},
        'calendar_path': None,
        'events_selected': 0,
        'trigger_bars': 0,
        'calendar_latest_utc': None,
        'coverage_gap_days': None,
    }
    if not NEWS_FILTER_ENABLED:
        return plan

    calendar_path = _resolve_news_calendar_path(NEWS_CALENDAR_FILE)
    if not calendar_path.exists():
        raise ValueError(f"NEWS_CALENDAR_FILE not found: {calendar_path}")
    calendar_stat = calendar_path.stat()
    cache_key = (
        id(data_index),
        len(data_index),
        data_index[0] if len(data_index) else None,
        data_index[-1] if len(data_index) else None,
        str(calendar_path),
        int(calendar_stat.st_mtime_ns),
        int(calendar_stat.st_size),
        int(NEWS_PRE_EVENT_MINUTES),
        tuple(str(val).strip() for val in NEWS_IMPACTS),
        tuple(str(val).strip().upper() for val in NEWS_BLOCK_CURRENCIES),
        NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS,
        bool(NEWS_REQUIRE_EVENTS_IN_DATA_RANGE),
    )
    cached_plan = _NEWS_PLAN_CACHE.get(cache_key)
    if cached_plan is not None:
        return cached_plan

    cal_sep = detect_csv_separator(calendar_path, file_label='NEWS_CALENDAR_FILE')
    cal_df = pd.read_csv(calendar_path, sep=cal_sep, dtype=str)
    required_cols = {'datetime_utc', 'impact', 'currency'}
    missing = sorted(required_cols - set(cal_df.columns))
    if missing:
        raise ValueError(
            f"NEWS_CALENDAR_FILE missing required columns: {missing}. "
            "Expected at least datetime_utc, impact, currency."
        )

    parsed_utc = pd.to_datetime(cal_df['datetime_utc'], errors='coerce', utc=True)
    invalid_count = int(parsed_utc.isna().sum())
    if invalid_count > 0:
        raise ValueError(
            f"NEWS_CALENDAR_FILE has {invalid_count} invalid datetime_utc rows. "
            "Fix calendar parsing before enabling NEWS_FILTER_ENABLED."
        )

    cal_df = cal_df.copy()
    cal_df['datetime_utc'] = parsed_utc.dt.tz_convert('UTC').dt.tz_localize(None)
    data_start = data_index.min()
    data_end = data_index.max()
    calendar_latest = cal_df['datetime_utc'].max()
    coverage_gap_days = None
    if pd.notna(calendar_latest):
        coverage_gap_days = float((data_end - calendar_latest).total_seconds() / 86400.0)
        if NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS is not None:
            if coverage_gap_days > float(NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS):
                raise ValueError(
                    "NEWS_CALENDAR_FILE is stale for this dataset range. "
                    f"Latest calendar event={calendar_latest}, data_end={data_end}, "
                    f"gap_days={coverage_gap_days:.2f}, "
                    f"allowed={float(NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS):.2f}."
                )

    # Currency logic:
    # - empty NEWS_BLOCK_CURRENCIES means "all currencies" (no currency mask),
    # - otherwise only selected currencies are considered.
    impact_set = {str(val).strip() for val in NEWS_IMPACTS if str(val).strip()}
    if not impact_set:
        raise ValueError("NEWS_IMPACTS is empty after normalization.")

    currency_set = {
        str(val).strip().upper() for val in NEWS_BLOCK_CURRENCIES if str(val).strip()
    }
    impact_mask = cal_df['impact'].astype(str).str.strip().isin(impact_set)
    if currency_set:
        currency_mask = cal_df['currency'].astype(str).str.strip().str.upper().isin(currency_set)
        selected = cal_df.loc[impact_mask & currency_mask, 'datetime_utc']
    else:
        selected = cal_df.loc[impact_mask, 'datetime_utc']

    pre_delta = pd.Timedelta(minutes=NEWS_PRE_EVENT_MINUTES)
    # Only keep events around available candle range. Historical older/newer events are ignored.
    events = selected.drop_duplicates().sort_values()
    events = events[(events >= (data_start - pre_delta)) & (events <= (data_end + pre_delta))]
    if NEWS_REQUIRE_EVENTS_IN_DATA_RANGE and len(events) == 0:
        raise ValueError(
            "No selected news events overlap the candle data range. "
            "Check NEWS_IMPACTS, NEWS_BLOCK_CURRENCIES, and dataset timezone."
        )

    trigger_map = {}
    for event_ts in events.tolist():
        # Find bars in pre-event interval [event-pre_delta, event).
        # left<right means at least one candle exists in that interval.
        left = int(data_index.searchsorted(event_ts - pre_delta, side='left'))
        right = int(data_index.searchsorted(event_ts, side='left'))
        if left < right:
            trigger_map.setdefault(left, set()).add(event_ts.normalize())

    plan.update(
        {
            'enabled': True,
            'trigger_map': trigger_map,
            'calendar_path': str(calendar_path),
            'events_selected': int(len(events)),
            'trigger_bars': int(len(trigger_map)),
            'calendar_latest_utc': calendar_latest.isoformat() if pd.notna(calendar_latest) else None,
            'coverage_gap_days': coverage_gap_days,
        }
    )
    _NEWS_PLAN_CACHE[cache_key] = plan
    return plan


def warn_on_bar_time_mismatch():
    if BAR_TIME_MODE != DATA_TIME_CONVENTION:
        print(
            "WARNING: Dataset DateTime is assumed to be bar "
            f"{DATA_TIME_CONVENTION} time, "
            f"but BAR_TIME_MODE is '{BAR_TIME_MODE}'. "
            "This will shift time-window logic by ~1 bar."
        )


def ema_filter_passes(direction, entry_price, ema_value):
    if not EMA_FILTER_ENABLED:
        return True
    if pd.isna(ema_value):
        return False
    if direction == 'LONG':
        return entry_price > ema_value
    return entry_price < ema_value


def pending_touched(high, low, close, direction, entry_price):
    tol = PRICE_TOUCH_TOLERANCE
    if PENDING_TOUCH_MODE == 'wick':
        return (low - tol) <= entry_price <= (high + tol)
    if direction == 'LONG':
        return close <= (entry_price + tol)
    return close >= (entry_price - tol)


def build_stop_and_tp(direction, entry_price, range_high, range_low, breakout_range):
    entry = float(entry_price)

    if SL_PLACEMENT == 'distance':
        raw_dist = float(breakout_range) * float(SL_MULTIPLIER)
        if pd.isna(raw_dist) or raw_dist <= 0:
            raw_dist = MIN_STOP_DISTANCE
        stop_dist = max(raw_dist, MIN_STOP_DISTANCE)
    else:
        # Range mode: "range" means the full session range size (high - low).
        base_range_dist = abs(float(range_high) - float(range_low))
        raw_dist = base_range_dist * float(SL_MULTIPLIER)
        if pd.isna(raw_dist) or raw_dist <= 0:
            raw_dist = MIN_STOP_DISTANCE
        stop_dist = max(raw_dist, MIN_STOP_DISTANCE)

    if direction == 'LONG':
        stop = entry - stop_dist
        tp = entry + (stop_dist * RR_RATIO)
    else:
        stop = entry + stop_dist
        tp = entry - (stop_dist * RR_RATIO)
    return stop, tp, stop_dist


def resolve_exit(direction, high, low, take_profit, stop_loss):
    if direction == 'LONG':
        tp_hit = high >= take_profit
        sl_hit = low <= stop_loss
    else:
        tp_hit = low <= take_profit
        sl_hit = high >= stop_loss

    if not tp_hit and not sl_hit:
        return None, None
    if tp_hit and sl_hit:
        if INTRABAR_EXIT_POLICY in {'tp_first', 'best_case'}:
            return 'TP', take_profit
        if INTRABAR_EXIT_POLICY in {'sl_first', 'worst_case'}:
            return 'SL', stop_loss
    if tp_hit:
        return 'TP', take_profit
    return 'SL', stop_loss


def resolve_exit_on_fill_bar(position, high, low):
    if FILL_BAR_EXIT_POLICY == 'ohlc':
        return resolve_exit(
            direction=position['direction'],
            high=high,
            low=low,
            take_profit=position['take_profit'],
            stop_loss=position['stop_loss'],
        )

    # After a pending entry is filled inside a candle, OHLC data cannot tell
    # whether the favorable extreme happened before or after the fill.
    # Count only adverse stop hits on the fill bar; defer TP to later candles.
    if position['direction'] == 'LONG':
        if low <= position['stop_loss']:
            return 'SL', position['stop_loss']
    else:
        if high >= position['stop_loss']:
            return 'SL', position['stop_loss']
    return None, None


def apply_breakeven(position, high_price, low_price, close_price, idx, ts):
    if not BREAKEVEN_ENABLED or position.get('be_moved', False):
        return False, False

    entry_price = position['entry_price']
    r = position.get('initial_stop_dist', abs(entry_price - position['stop_loss']))
    if r <= 0:
        return False, False

    if position['direction'] == 'LONG':
        trigger_price = entry_price + (r * BREAKEVEN_TRIGGER_R)
        trigger_hit = (
            high_price >= trigger_price
            if BREAKEVEN_TRIGGER_MODE == 'wick'
            else close_price >= trigger_price
        )
    else:
        trigger_price = entry_price - (r * BREAKEVEN_TRIGGER_R)
        trigger_hit = (
            low_price <= trigger_price
            if BREAKEVEN_TRIGGER_MODE == 'wick'
            else close_price <= trigger_price
        )

    triggered_now = False
    moved_now = False
    if trigger_hit and not position.get('be_triggered', False):
        position['be_triggered'] = True
        position['be_trigger_time'] = ts
        position['be_trigger_idx'] = idx
        triggered_now = True

    if position.get('be_triggered', False):
        trigger_idx = position.get('be_trigger_idx', idx)
        can_apply = BREAKEVEN_ALLOW_SAME_BAR or idx > trigger_idx
        if can_apply:
            offset_value = BREAKEVEN_OFFSET * r
            if position['direction'] == 'LONG':
                new_stop = entry_price + offset_value
                new_stop = min(new_stop, position['take_profit'])
                if new_stop > position['stop_loss']:
                    position['stop_loss'] = new_stop
                    position['be_stop'] = new_stop
                    moved_now = True
            else:
                new_stop = entry_price - offset_value
                new_stop = max(new_stop, position['take_profit'])
                if new_stop < position['stop_loss']:
                    position['stop_loss'] = new_stop
                    position['be_stop'] = new_stop
                    moved_now = True
            if moved_now:
                position['be_moved'] = True
                position['be_move_time'] = ts
                position['be_move_idx'] = idx

    return triggered_now, moved_now


def update_trailing_stop_after_bar(position, high_price, low_price, close_price, idx, ts):
    # This is called only after the current bar has survived the old SL/TP.
    # The new trailed stop therefore becomes effective from the next bar.
    if not TRAILING_SL_ENABLED:
        return False, False

    entry_price = float(position['entry_price'])
    r = float(position.get('initial_stop_dist', abs(entry_price - float(position['stop_loss']))))
    if r <= 0:
        return False, False

    direction = position['direction']
    use_wick = TRAILING_SL_TRIGGER_MODE == 'wick'
    if direction == 'LONG':
        reference_price = float(high_price if use_wick else close_price)
        activation_price = entry_price + (r * float(TRAILING_SL_ACTIVATE_R))
        activation_hit = reference_price >= activation_price
    else:
        reference_price = float(low_price if use_wick else close_price)
        activation_price = entry_price - (r * float(TRAILING_SL_ACTIVATE_R))
        activation_hit = reference_price <= activation_price

    activated_now = False
    moved_now = False
    if not position.get('trail_activated', False):
        if not activation_hit:
            return False, False
        position['trail_activated'] = True
        position['trail_activation_time'] = ts
        position['trail_activation_idx'] = idx
        position['trail_best_price'] = reference_price
        activated_now = True
    else:
        previous_best = position.get('trail_best_price', reference_price)
        if direction == 'LONG':
            position['trail_best_price'] = max(float(previous_best), reference_price)
        else:
            position['trail_best_price'] = min(float(previous_best), reference_price)

    best_price = float(position.get('trail_best_price', reference_price))
    trail_distance = float(TRAILING_SL_DISTANCE_R) * r
    if direction == 'LONG':
        new_stop = best_price - trail_distance
        if TRAILING_SL_LOCK_BREAKEVEN:
            new_stop = max(new_stop, entry_price)
        if TRAILING_SL_CAP_AT_TP:
            new_stop = min(new_stop, float(position['take_profit']))
    else:
        new_stop = best_price + trail_distance
        if TRAILING_SL_LOCK_BREAKEVEN:
            new_stop = min(new_stop, entry_price)
        if TRAILING_SL_CAP_AT_TP:
            new_stop = max(new_stop, float(position['take_profit']))

    current_stop = float(position['stop_loss'])
    min_step = float(TRAILING_SL_STEP_R) * r
    if _better_stop(direction, new_stop, current_stop):
        if min_step <= 0 or _stop_improvement_enough(direction, new_stop, current_stop, min_step):
            position['stop_loss'] = float(new_stop)
            position['trail_stop'] = float(new_stop)
            position['trail_moved'] = True
            position['trail_move_time'] = ts
            position['trail_move_idx'] = idx
            position['trail_move_count'] = int(position.get('trail_move_count', 0)) + 1
            moved_now = True

    return activated_now, moved_now


def close_trade(position, exit_time, exit_idx, exit_price, exit_reason):
    trade = position.copy()
    trade['exit_time'] = exit_time
    trade['exit_idx'] = exit_idx
    trade['exit_price'] = exit_price
    trade['exit_reason'] = exit_reason
    if trade['direction'] == 'LONG':
        gross_pnl = (exit_price - trade['entry_price']) * trade['position_size']
    else:
        gross_pnl = (trade['entry_price'] - exit_price) * trade['position_size']
    trade_cost = float(BACKTEST_COST_PER_TRADE) + (float(BACKTEST_COST_POINTS) * float(trade['position_size']))
    trade['gross_pnl'] = gross_pnl
    trade['trade_cost'] = trade_cost
    trade['pnl'] = gross_pnl - trade_cost
    return trade


def _dynamic_risk_step(trades):
    """Count the active win/loss streak for the two classic progressive modes."""
    settings = RISK_SETTINGS
    if not settings.get('enabled', False) or not trades:
        return 0
    mode = str(settings.get('mode', '')).lower()
    if mode not in {'martingale', 'anti_martingale'}:
        return 0
    wants_positive = mode == 'anti_martingale'
    step = 0
    for trade in reversed(trades):
        pnl = float(trade.get('pnl', 0.0))
        if (pnl > 0) != wants_positive:
            break
        step += 1
    return step


def _anti_martingale_ladder_risk(base_risk_amount, trades):
    """Calculate the next risk from closed PnL using the simple ladder mode.

    Example (base=20, win_step=15, min=5, max=50):
    20 -> win 35 -> win 50 -> loss 20 -> loss 10 -> loss 5.
    """
    settings = RISK_SETTINGS
    base_risk = float(base_risk_amount)
    floor = float(settings.get('min_risk', 0.0))
    ceiling = float(settings.get('max_risk', float('inf')))
    risk = min(max(base_risk, floor), ceiling)
    increment = float(settings.get('win_step', 0.0))
    loss_multiplier = float(settings.get('loss_multiplier', 1.0))

    for trade in trades:
        pnl = float(trade.get('pnl', 0.0))
        if pnl > 0:
            risk = min(risk + increment, ceiling)
        elif pnl < 0:
            # A loss after an elevated win-run returns to the user-defined
            # base risk. Later losses then step down to the configured floor.
            if risk > base_risk:
                risk = min(max(base_risk, floor), ceiling)
            else:
                risk = max(risk * loss_multiplier, floor)
    return risk


def _prop_equity_state(trades, current_day):
    """Build realised all-time and daily equity state without using future bars."""
    equity = float(STARTING_BALANCE)
    peak_equity = equity
    day_start_equity = equity
    day_peak_equity = equity
    active_day = None
    for trade in trades:
        trade_time = trade.get('exit_time', trade.get('entry_time'))
        if trade_time is None:
            continue
        trade_day = pd.Timestamp(trade_time).normalize()
        if active_day is None or trade_day != active_day:
            active_day = trade_day
            day_start_equity = equity
            day_peak_equity = equity
        equity += float(trade.get('pnl', 0.0))
        peak_equity = max(peak_equity, equity)
        day_peak_equity = max(day_peak_equity, equity)

    requested_day = pd.Timestamp(current_day).normalize()
    if active_day is None or requested_day != active_day:
        day_start_equity = equity
        day_peak_equity = equity
    return {
        'equity': equity,
        'peak_equity': peak_equity,
        'day_start_equity': day_start_equity,
        'day_peak_equity': day_peak_equity,
    }


def resolve_prop_safe_risk(base_risk_amount, stop_dist, trades, current_day):
    """Cap a proposed risk amount before order creation using realised prop DD room.

    The stop distance is used to include configured point-based execution costs.
    A rejected decision is intentionally returned instead of opening a zero-size
    position, making the protection auditable in the final trade report.
    """
    settings = RISK_SETTINGS
    base_risk = max(float(base_risk_amount), 0.0)
    mode = str(settings.get('mode', 'fixed')).lower()
    step = _dynamic_risk_step(trades)
    multiplier = float(settings.get('step_multiplier', 1.0)) ** step if settings.get('enabled', False) else 1.0
    if settings.get('enabled', False) and mode == 'anti_martingale':
        proposed_risk = _anti_martingale_ladder_risk(base_risk, trades)
        multiplier = (proposed_risk / base_risk) if base_risk > 0 else 1.0
    else:
        proposed_risk = base_risk * multiplier
    if settings.get('enabled', False):
        proposed_risk = min(proposed_risk, float(settings.get('max_risk_amount', float('inf'))))
    state = _prop_equity_state(trades, current_day)
    equity = state['equity']
    per_trade_cap = (
        equity * (float(settings.get('max_risk_per_trade_pct', 100.0)) / 100.0)
        if settings.get('enabled', False)
        else float('inf')
    )
    allowed_total_loss = per_trade_cap
    reasons = []

    if settings.get('enabled', False) and settings.get('enforce_prop_limits', True) and PROP_ENABLED:
        buffer_pct = float(settings.get('prop_safety_buffer_pct', 0.0)) / 100.0
        max_dd = _resolve_prop_value(PROP_MAX_DD_PCT, PROP_MAX_DD_ABS, STARTING_BALANCE)
        max_dd_reference = state['peak_equity'] if PROP_MAX_DD_MODE == 'trailing' else float(STARTING_BALANCE)
        remaining_max_dd = max_dd - (max_dd_reference - equity) - (max_dd * buffer_pct)
        allowed_total_loss = min(allowed_total_loss, remaining_max_dd)
        if remaining_max_dd <= 0:
            reasons.append('MAX_DD_LIMIT')

        daily_dd = _resolve_prop_value(PROP_DAILY_DD_PCT, PROP_DAILY_DD_ABS, STARTING_BALANCE)
        if daily_dd is not None:
            daily_reference = state['day_peak_equity'] if PROP_DAILY_DD_MODE == 'peak' else state['day_start_equity']
            remaining_daily_dd = daily_dd - (daily_reference - equity) - (daily_dd * buffer_pct)
            allowed_total_loss = min(allowed_total_loss, remaining_daily_dd)
            if remaining_daily_dd <= 0:
                reasons.append('DAILY_DD_LIMIT')

    # Loss at the stop includes the existing fixed/point execution-cost model.
    cost_factor = 1.0 + (float(BACKTEST_COST_POINTS) / max(float(stop_dist), MIN_STOP_DISTANCE))
    max_risk_after_costs = (allowed_total_loss - float(BACKTEST_COST_PER_TRADE)) / cost_factor
    approved_risk = min(proposed_risk, per_trade_cap, max_risk_after_costs)
    min_risk = float(settings.get('min_risk_amount', 0.0))
    if approved_risk < min_risk or approved_risk <= 0:
        reasons.append('INSUFFICIENT_PROP_RISK_BUDGET')
        return {
            'allowed': False,
            'risk_amount': 0.0,
            'step': step,
            'multiplier': multiplier,
            'reason': '|'.join(dict.fromkeys(reasons)),
            'state': state,
        }
    return {
        'allowed': True,
        'risk_amount': approved_risk,
        'step': step,
        'multiplier': multiplier,
        'reason': 'CAPPED_BY_PROP_GUARD' if approved_risk < proposed_risk else 'OK',
        'state': state,
    }


def _order_trades_for_stats(trades_table):
    if trades_table is None or trades_table.empty:
        return trades_table
    if 'exit_time' in trades_table.columns:
        return trades_table.sort_values('exit_time').reset_index(drop=True)
    if 'entry_time' in trades_table.columns:
        return trades_table.sort_values('entry_time').reset_index(drop=True)
    return trades_table.reset_index(drop=True)


def _compute_drawdown_stats_from_pnls(pnl_series, starting_balance):
    if pnl_series is None:
        return 0.0, 0.0

    pnl_values = pd.Series(pnl_series, dtype='float64').reset_index(drop=True)
    if pnl_values.empty:
        return 0.0, 0.0

    equity_after = float(starting_balance) + pnl_values.cumsum()
    equity_path = pd.concat(
        [pd.Series([float(starting_balance)], dtype='float64'), equity_after],
        ignore_index=True,
    )
    peak = equity_path.cummax()
    drawdown = equity_path - peak
    max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0
    peak_safe = peak.replace(0, pd.NA)
    drawdown_pct = (drawdown / peak_safe).dropna()
    max_drawdown_pct = abs(float(drawdown_pct.min()) * 100.0) if not drawdown_pct.empty else 0.0
    return max_drawdown, max_drawdown_pct


def _pending_fill_bars_seen(created_idx, current_idx):
    if ALLOW_SAME_BAR_FILL:
        return (int(current_idx) - int(created_idx)) + 1
    return int(current_idx) - int(created_idx)


def get_data_cache(data, ema_filter_enabled=None, ema_period=None):
    if data is None or data.empty:
        return None
    if ema_filter_enabled is None:
        ema_filter_enabled = EMA_FILTER_ENABLED
    if ema_filter_enabled:
        ema_period = int(ema_period if ema_period is not None else EMA_PERIOD)
    else:
        ema_period = 0
    # Include EMA state in cache key so grid EMA_PERIOD / EMA_FILTER changes are respected.
    ema_enabled_key = bool(ema_filter_enabled)
    ema_period_key = int(ema_period) if ema_enabled_key else 0
    key = (id(data), len(data), data.index[0], data.index[-1], ema_enabled_key, ema_period_key)
    cached = _DATA_CACHE.get(key)
    if cached:
        return cached

    close_values = data['close'].to_numpy()
    ema_values = None
    if ema_filter_enabled:
        ema_values = pd.Series(close_values, index=data.index).ewm(
            span=ema_period,
            adjust=False,
        ).mean().to_numpy()

    cache = {
        'index_values': data.index,
        'open_values': data['open'].to_numpy(),
        'high_values': data['high'].to_numpy(),
        'low_values': data['low'].to_numpy(),
        'close_values': close_values,
        'ema_values': ema_values,
        'day_values': data['day'].tolist(),
        'time_values': data['time'].tolist(),
    }
    _DATA_CACHE[key] = cache
    return cache


def compute_trade_stats(trades_table, starting_balance=None):
    if trades_table is None or trades_table.empty:
        return {
            'total_trades': 0,
            'flat_trades': 0,
            'win_rate': 0.0,
            'profit_factor': float('nan'),
            'sharpe': float('nan'),
            'total_pnl': 0.0,
            'avg_pnl': float('nan'),
            'avg_win': float('nan'),
            'avg_loss': float('nan'),
            'payoff_ratio': float('nan'),
            'expectancy': float('nan'),
            'avg_r': float('nan'),
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0,
            'max_drawdown': 0.0,
            'max_drawdown_pct': 0.0,
        }

    if starting_balance is None:
        starting_balance = STARTING_BALANCE

    ordered_trades = _order_trades_for_stats(trades_table)
    total_trades = len(ordered_trades)
    wins = ordered_trades[ordered_trades['pnl'] > 0]
    losses = ordered_trades[ordered_trades['pnl'] < 0]
    flats = ordered_trades[ordered_trades['pnl'] == 0]
    win_rate = (len(wins) / total_trades) * 100.0 if total_trades else 0.0
    total_pnl = ordered_trades['pnl'].sum()
    avg_pnl = ordered_trades['pnl'].mean()

    gross_profit = wins['pnl'].sum()
    gross_loss = abs(losses['pnl'].sum())
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float('inf')
    else:
        profit_factor = float('nan')

    avg_win = wins['pnl'].mean() if not wins.empty else float('nan')
    avg_loss = losses['pnl'].mean() if not losses.empty else float('nan')
    avg_loss_abs = abs(avg_loss) if pd.notna(avg_loss) else float('nan')
    payoff_ratio = float('nan')
    if pd.notna(avg_win) and pd.notna(avg_loss_abs):
        payoff_ratio = (avg_win / avg_loss_abs) if avg_loss_abs > 0 else float('inf')
    loss_rate = (len(losses) / total_trades) if total_trades else 0.0
    expectancy = (win_rate / 100.0) * avg_win + loss_rate * avg_loss if pd.notna(avg_win) and pd.notna(avg_loss) else float('nan')
    if total_trades:
        expectancy = total_pnl / total_trades

    equity_before = starting_balance + ordered_trades['pnl'].cumsum().shift(1).fillna(0.0)
    returns = ordered_trades['pnl'] / equity_before.replace(0, pd.NA)
    returns = returns.dropna()
    sharpe = (returns.mean() / returns.std()) * (len(returns) ** 0.5) if len(returns) > 1 and returns.std() != 0 else float('nan')

    avg_r = float('nan')
    if 'initial_stop_dist' in ordered_trades.columns and 'position_size' in ordered_trades.columns:
        risk = ordered_trades['initial_stop_dist'] * ordered_trades['position_size']
        risk = risk.replace(0, pd.NA)
        r_values = ordered_trades['pnl'] / risk
        r_values = r_values.dropna()
        if not r_values.empty:
            avg_r = r_values.mean()

    max_consec_wins = 0
    max_consec_losses = 0
    cur_wins = 0
    cur_losses = 0
    for pnl in ordered_trades['pnl'].tolist():
        if pnl > 0:
            cur_wins += 1
            cur_losses = 0
        elif pnl < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        max_consec_wins = max(max_consec_wins, cur_wins)
        max_consec_losses = max(max_consec_losses, cur_losses)

    max_drawdown, max_drawdown_pct = _compute_drawdown_stats_from_pnls(
        ordered_trades['pnl'],
        starting_balance=starting_balance,
    )

    return {
        'total_trades': total_trades,
        'flat_trades': int(len(flats)),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'sharpe': sharpe,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'payoff_ratio': payoff_ratio,
        'expectancy': expectancy,
        'avg_r': avg_r,
        'max_consecutive_wins': max_consec_wins,
        'max_consecutive_losses': max_consec_losses,
        'max_drawdown': max_drawdown,
        'max_drawdown_pct': max_drawdown_pct,
    }


def _format_hhmm(hour_value, minute_value):
    return f"{int(hour_value):02d}:{int(minute_value):02d}"


def _cap_pf_for_score(value, cap=5.0):
    if pd.isna(value):
        return None
    val = float(value)
    if val == float('inf'):
        return float(cap)
    if val < 0:
        return 0.0
    return val


def _positive_score_component(value, cap):
    if pd.isna(value):
        return 0.0
    val = max(float(value), 0.0)
    return min(val, float(cap)) / float(cap)


def _finite_rank_value(value, cap=None):
    if pd.isna(value):
        return float('-inf')
    val = float(value)
    if val == float('inf'):
        return float(cap if cap is not None else GRID_INF_METRIC_CAP)
    if val == float('-inf'):
        return float('-inf')
    return val


def _trade_count_from_row(row, column_name):
    value = row.get(column_name, 0)
    if pd.isna(value):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _infer_timeframe_minutes_from_index(index_values):
    # Infer dominant bar step from candle index (ignoring large session/weekend gaps).
    if index_values is None or len(index_values) < 2:
        return 1
    diffs = index_values.to_series().diff().dropna().dt.total_seconds() / 60.0
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return 1
    diffs_rounded = diffs.round().astype(int)
    intraday = diffs_rounded[diffs_rounded <= 240]
    base = intraday if not intraday.empty else diffs_rounded
    if base.empty:
        return 1
    mode_vals = base.mode()
    if not mode_vals.empty:
        return max(1, int(mode_vals.iloc[0]))
    return max(1, int(base.median()))


def _resolve_shift_minutes_from_data(
    index_values,
    use_data_driven_shift,
    shift_bars,
    max_shift_minutes,
    step_minutes,
):
    detected_step = _infer_timeframe_minutes_from_index(index_values)
    if use_data_driven_shift:
        resolved_step = int(detected_step)
        resolved_max = int(max(resolved_step, int(shift_bars) * resolved_step))
    else:
        resolved_step = int(max(1, step_minutes))
        resolved_max = int(max(resolved_step, max_shift_minutes))
    # Keep max aligned to step grid.
    resolved_max = int((resolved_max // resolved_step) * resolved_step)
    if resolved_max < resolved_step:
        resolved_max = resolved_step
    return detected_step, resolved_step, resolved_max


def _build_random_shift_offsets(count, max_shift_minutes, step_minutes, seed):
    candidates = [x for x in range(-max_shift_minutes, max_shift_minutes + 1, step_minutes) if x != 0]
    if not candidates:
        return []
    rng = random.Random(seed)
    rng.shuffle(candidates)
    if count >= len(candidates):
        return candidates
    return candidates[:count]


def _shift_window_by_minutes(start_h, start_m, end_h, end_m, shift_minutes, trade_end_minutes):
    start_total = (int(start_h) * 60) + int(start_m) + int(shift_minutes)
    end_total = (int(end_h) * 60) + int(end_m) + int(shift_minutes)
    if start_total < 0 or end_total < 0:
        return None
    if start_total >= end_total:
        return None
    if end_total >= trade_end_minutes:
        return None
    if end_total > ((23 * 60) + 59):
        return None
    return (
        int(start_total // 60),
        int(start_total % 60),
        int(end_total // 60),
        int(end_total % 60),
    )


def evaluate_window_robustness(
    data,
    base_window,
    base_stats,
    shift_count,
    use_data_driven_shift,
    shift_bars,
    max_shift_minutes,
    step_minutes,
    seed,
    min_trades,
    pf_pass_threshold,
):
    # Robustness workflow:
    # 1) Randomly shift base window by small minute offsets.
    # 2) Re-run backtest for each shifted window.
    # 3) Score how often performance survives shifts (less timestamp-overfit).
    if data is None or data.empty:
        return None

    start_h, start_m, end_h, end_m = base_window
    trade_end_minutes = (TRADE_END_HOUR * 60) + TRADE_END_MINUTE
    detected_step, resolved_step, resolved_max = _resolve_shift_minutes_from_data(
        index_values=data.index,
        use_data_driven_shift=use_data_driven_shift,
        shift_bars=shift_bars,
        max_shift_minutes=max_shift_minutes,
        step_minutes=step_minutes,
    )
    seed_material = (
        int(seed)
        + (int(start_h) * 100000)
        + (int(start_m) * 1000)
        + (int(end_h) * 100)
        + int(end_m)
        + int(SL_MULTIPLIER * 1000)
        + int(RR_RATIO * 1000)
        + (detected_step * 17)
        + (resolved_step * 31)
        + (resolved_max * 53)
    )
    offsets = _build_random_shift_offsets(
        count=int(shift_count),
        max_shift_minutes=int(resolved_max),
        step_minutes=int(resolved_step),
        seed=seed_material,
    )
    if not offsets:
        return None

    rows = []
    original_window = (RANGE_START_HOUR, RANGE_START_MINUTE, RANGE_END_HOUR, RANGE_END_MINUTE)
    try:
        for offset in offsets:
            shifted = _shift_window_by_minutes(
                start_h,
                start_m,
                end_h,
                end_m,
                shift_minutes=offset,
                trade_end_minutes=trade_end_minutes,
            )
            if shifted is None:
                rows.append(
                    {
                        'shift_min': int(offset),
                        'range_start': None,
                        'range_end': None,
                        'status': 'SKIPPED_INVALID',
                        'total_trades': 0,
                        'profit_factor': float('nan'),
                        'sharpe': float('nan'),
                        'total_pnl': float('nan'),
                        'max_drawdown_pct': float('nan'),
                    }
                )
                continue

            set_range_window(*shifted)
            try:
                shifted_trades = run_backtest(
                    data,
                    silent=True,
                    save_trades=False,
                    show_plots=False,
                    run_distribution=False,
                    run_curve_check=False,
                    run_window_robustness=False,
                )
                shifted_stats = compute_trade_stats(shifted_trades)
                rows.append(
                    {
                        'shift_min': int(offset),
                        'range_start': _format_hhmm(shifted[0], shifted[1]),
                        'range_end': _format_hhmm(shifted[2], shifted[3]),
                        'status': 'OK',
                        'total_trades': int(shifted_stats['total_trades']),
                        'profit_factor': float(shifted_stats['profit_factor']) if pd.notna(shifted_stats['profit_factor']) else float('nan'),
                        'sharpe': float(shifted_stats['sharpe']) if pd.notna(shifted_stats['sharpe']) else float('nan'),
                        'total_pnl': float(shifted_stats['total_pnl']),
                        'max_drawdown_pct': float(shifted_stats['max_drawdown_pct']),
                    }
                )
            except Exception:
                rows.append(
                    {
                        'shift_min': int(offset),
                        'range_start': _format_hhmm(shifted[0], shifted[1]),
                        'range_end': _format_hhmm(shifted[2], shifted[3]),
                        'status': 'ERROR',
                        'total_trades': 0,
                        'profit_factor': float('nan'),
                        'sharpe': float('nan'),
                        'total_pnl': float('nan'),
                        'max_drawdown_pct': float('nan'),
                    }
                )
    finally:
        set_range_window(*original_window)

    rows_df = pd.DataFrame(rows)
    if rows_df.empty:
        return None

    shiftable_count = int((rows_df['status'] != 'SKIPPED_INVALID').sum())
    executed_count = int((rows_df['status'] == 'OK').sum())
    error_count = int((rows_df['status'] == 'ERROR').sum())
    valid_df = rows_df[
        (rows_df['status'] == 'OK')
        & (rows_df['total_trades'] >= int(min_trades))
    ].copy()
    valid_df['pf_capped'] = valid_df['profit_factor'].apply(_cap_pf_for_score)
    valid_df = valid_df[valid_df['pf_capped'].notna()]

    pf_values = valid_df['pf_capped'].tolist()
    valid_count = int(len(pf_values))
    if valid_count == 0:
        robustness_score = 0.0
        pf_pass_ratio = 0.0
        pnl_pass_ratio = 0.0
        median_pf = float('nan')
        pf_std = float('nan')
        stability = 0.0
        base_pf_percentile = float('nan')
    else:
        pf_pass_ratio = float((valid_df['pf_capped'] >= float(pf_pass_threshold)).mean())
        pnl_pass_ratio = float((valid_df['total_pnl'] > 0).mean())
        median_pf = float(pd.Series(pf_values).median())
        pf_std = float(pd.Series(pf_values).std(ddof=0)) if len(pf_values) > 1 else 0.0
        stability = 1.0 / (1.0 + max(pf_std, 0.0))
        valid_ratio = float(valid_count / max(shiftable_count, 1))
        median_pf_score = min(max(median_pf, 0.0) / 2.0, 1.0)
        robustness_score = 100.0 * (
            (0.35 * pf_pass_ratio)
            + (0.25 * pnl_pass_ratio)
            + (0.20 * median_pf_score)
            + (0.20 * (valid_ratio * stability))
        )
        base_pf = _cap_pf_for_score(base_stats.get('profit_factor', float('nan')))
        if base_pf is None:
            base_pf_percentile = float('nan')
        else:
            base_pf_percentile = float((sum(1 for x in pf_values if x <= base_pf) / len(pf_values)) * 100.0)

    return {
        'base_window': (
            int(start_h),
            int(start_m),
            int(end_h),
            int(end_m),
        ),
        'data_timeframe_minutes': int(detected_step),
        'resolved_shift_step_minutes': int(resolved_step),
        'resolved_max_shift_minutes': int(resolved_max),
        'use_data_driven_shift': bool(use_data_driven_shift),
        'attempted_shifts': int(len(offsets)),
        'shiftable_shifts': shiftable_count,
        'executed_shifts': executed_count,
        'error_shifts': error_count,
        'valid_shifts': valid_count,
        'pf_pass_ratio': pf_pass_ratio,
        'pnl_pass_ratio': pnl_pass_ratio,
        'median_shift_pf': median_pf,
        'pf_std': pf_std,
        'stability_factor': stability,
        'base_pf_percentile': base_pf_percentile,
        'robustness_score': float(max(0.0, min(robustness_score, 100.0))),
        'rows': rows_df,
    }


def split_trades_by_pct(trades_table, train_pct):
    if trades_table is None or trades_table.empty:
        return None, None, None
    sorted_times = trades_table['entry_time'].sort_values()
    split_idx = int(len(sorted_times) * train_pct)
    if split_idx <= 0 or split_idx >= len(sorted_times):
        return None, None, None
    split_ts = pd.to_datetime(sorted_times.iloc[split_idx])
    is_trades = trades_table[trades_table['entry_time'] < split_ts]
    oos_trades = trades_table[trades_table['entry_time'] >= split_ts]
    return split_ts, is_trades, oos_trades


def compute_grid_split_stats(trades_table, train_pct):
    if trades_table is None or trades_table.empty:
        return None
    if CURVE_SPLIT_DATE:
        split_ts = pd.to_datetime(CURVE_SPLIT_DATE)
        is_trades = trades_table[trades_table['entry_time'] < split_ts]
        oos_trades = trades_table[trades_table['entry_time'] >= split_ts]
    else:
        split_ts, is_trades, oos_trades = split_trades_by_pct(trades_table, train_pct)
        if split_ts is None:
            return None
    is_stats = compute_trade_stats(is_trades)
    oos_stats = compute_trade_stats(oos_trades)
    if is_stats['total_trades'] < CURVE_MIN_TRADES or oos_stats['total_trades'] < CURVE_MIN_TRADES:
        return None
    return {
        'split_date': str(split_ts.date()),
        'is_trades': is_stats['total_trades'],
        'oos_trades': oos_stats['total_trades'],
        'oos_profit_factor': oos_stats['profit_factor'],
        'oos_win_rate': oos_stats['win_rate'],
        'oos_total_pnl': oos_stats['total_pnl'],
    }


def compute_weekday_stats(trades_table):
    if trades_table is None or trades_table.empty:
        return None

    df = trades_table.copy()
    df['weekday'] = df['entry_time'].dt.dayofweek
    names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    rows = []
    for idx, name in enumerate(names):
        sub = df[df['weekday'] == idx]
        total = len(sub)
        if total == 0:
            rows.append(
                {
                    'weekday': name,
                    'total_trades': 0,
                    'win_rate': 0.0,
                    'profit_factor': float('nan'),
                    'total_pnl': 0.0,
                    'avg_pnl': float('nan'),
                }
            )
            continue

        wins = sub[sub['pnl'] > 0]
        losses = sub[sub['pnl'] < 0]
        win_rate = (len(wins) / total) * 100.0
        gross_profit = wins['pnl'].sum()
        gross_loss = abs(losses['pnl'].sum())
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float('inf')
        else:
            profit_factor = float('nan')
        total_pnl = sub['pnl'].sum()
        avg_pnl = sub['pnl'].mean()

        rows.append(
            {
                'weekday': name,
                'total_trades': total,
                'win_rate': win_rate,
                'profit_factor': profit_factor,
                'total_pnl': total_pnl,
                'avg_pnl': avg_pnl,
            }
        )

    return pd.DataFrame(rows)


def compute_monthly_returns(trades_table, starting_balance=None):
    if trades_table is None or trades_table.empty:
        return None
    if starting_balance is None:
        starting_balance = STARTING_BALANCE

    time_col = 'exit_time' if 'exit_time' in trades_table.columns else 'entry_time'
    df = trades_table.sort_values(time_col).copy()

    if 'equity_before' not in df.columns or 'equity_after' not in df.columns:
        df['equity_after'] = starting_balance + df['pnl'].cumsum()
        df['equity_before'] = df['equity_after'] - df['pnl']

    df['month'] = df[time_col].dt.to_period('M').dt.to_timestamp()
    rows = []
    prev_end = starting_balance
    for month, mdf in df.groupby('month'):
        start_equity = float(mdf.iloc[0]['equity_before'])
        end_equity = float(mdf.iloc[-1]['equity_after'])
        if pd.isna(start_equity):
            start_equity = prev_end
        if start_equity == 0:
            max_pct = float('nan')
        else:
            max_equity = max(float(start_equity), float(mdf['equity_after'].max()))
            max_pct = ((max_equity / start_equity) - 1.0) * 100.0
        if start_equity == 0:
            return_pct = float('nan')
        else:
            return_pct = ((end_equity / start_equity) - 1.0) * 100.0
        rows.append(
            {
                'month': month,
                'start_equity': start_equity,
                'end_equity': end_equity,
                'return_pct': return_pct,
                'max_pct': max_pct,
            }
        )
        prev_end = end_equity

    return pd.DataFrame(rows)


def compute_distribution(data, trades_table, mode, horizon_bars):
    if trades_table is None or trades_table.empty:
        return None

    mfes = []
    maes = []
    hit_1r = 0
    hit_2r = 0
    hit_3r = 0

    # Precompute session end index per day for 'session' mode
    session_end_idx_by_day = None
    if mode == 'session':
        session_end_idx_by_day = {}
        for day, day_df in data.groupby('day'):
            if BAR_TIME_MODE == 'close':
                day_session = day_df[day_df['time'] <= TRADE_END_TIME]
            else:
                day_session = day_df[day_df['time'] < TRADE_END_TIME]
            if day_session.empty:
                continue
            session_end_idx_by_day[day] = int(day_session.index.map(data.index.get_loc).max())

    for trade in trades_table.to_dict('records'):
        entry_idx = int(trade['entry_idx'])
        exit_idx = int(trade['exit_idx'])
        if entry_idx < 0 or entry_idx >= len(data):
            continue

        if mode == 'trade':
            if exit_idx < entry_idx:
                continue
            end_idx = exit_idx
        elif mode == 'bars':
            end_idx = min(entry_idx + horizon_bars - 1, len(data) - 1)
        else:
            trade_day = pd.Timestamp(trade['entry_time']).normalize()
            end_idx = session_end_idx_by_day.get(trade_day, None)
            if end_idx is None or end_idx < entry_idx:
                continue
        entry_price = float(trade['entry_price'])
        stop_loss = float(trade['stop_loss'])
        r = abs(entry_price - stop_loss) #değişiklik yaptım aslında orjinal buydu r = float(trade.get('initial_stop_dist', abs(entry_price - stop_loss))) if r <= 0: continue 
        if r <= 1e-9:

           continue

        window = data.iloc[entry_idx:end_idx + 1]
        if window.empty:
            continue
        max_high = window['high'].max()
        min_low = window['low'].min()

        if trade['direction'] == 'LONG':
            mfe = (max_high - entry_price) / r
            mae = (entry_price - min_low) / r
        else:
            mfe = (entry_price - min_low) / r
            mae = (max_high - entry_price) / r

        mfes.append(mfe)
        maes.append(mae)
        if mfe >= 1.0:
            hit_1r += 1
        if mfe >= 2.0:
            hit_2r += 1
        if mfe >= 3.0:
            hit_3r += 1

    if not mfes:
        return None

    total = len(mfes)
    return {
        'count': total,
        'avg_mfe_r': sum(mfes) / total,
        'avg_mae_r': sum(maes) / total,
        'hit_1r_pct': (hit_1r / total) * 100.0,
        'hit_2r_pct': (hit_2r / total) * 100.0,
        'hit_3r_pct': (hit_3r / total) * 100.0,
    }


def compute_max_drawdown_pct_from_pnls(pnls, start_balance):
    if not pnls:
        return 0.0
    equity = start_balance
    peak = start_balance
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (equity - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return abs(max_dd * 100.0)


def monte_carlo_dd(trades_table, iterations, seed):
    if trades_table is None or trades_table.empty or iterations < 1:
        return None
    ordered = _order_trades_for_stats(trades_table)
    if len(ordered) < 2:
        return None
    rng = random.Random(seed)
    dd_values = []
    use_r_sim = (
        RISK_MODE == 'percent'
        and 'initial_stop_dist' in ordered.columns
        and 'position_size' in ordered.columns
    )
    if use_r_sim:
        risk = (ordered['initial_stop_dist'] * ordered['position_size']).replace(0, pd.NA)
        r_values = (ordered['pnl'] / risk).dropna().tolist()
        if len(r_values) < 2:
            use_r_sim = False
    if use_r_sim:
        risk_fraction = float(RISK_PERCENT_PER_TRADE) / 100.0
        for _ in range(iterations):
            shuffled = r_values[:]
            rng.shuffle(shuffled)
            equity = float(STARTING_BALANCE)
            peak = float(STARTING_BALANCE)
            max_dd = 0.0
            for r_value in shuffled:
                pnl = equity * risk_fraction * float(r_value)
                equity += pnl
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = min(max_dd, (equity - peak) / peak)
            dd_values.append(abs(max_dd * 100.0))
    else:
        pnls = ordered['pnl'].tolist()
        if len(pnls) < 2:
            return None
        for _ in range(iterations):
            shuffled = pnls[:]
            rng.shuffle(shuffled)
            dd_values.append(compute_max_drawdown_pct_from_pnls(shuffled, STARTING_BALANCE))
    dd_values.sort()
    p50 = dd_values[int(0.5 * (len(dd_values) - 1))]
    p95 = dd_values[int(0.95 * (len(dd_values) - 1))]
    p99 = dd_values[int(0.99 * (len(dd_values) - 1))]
    return {'p50': p50, 'p95': p95, 'p99': p99, 'max': dd_values[-1]}


def walk_forward_oos_pf(trades_table, train_days, test_days, step_days, pf_min):
    if trades_table is None or trades_table.empty:
        return None
    if train_days < 1 or test_days < 1 or step_days < 1:
        return None

    entries = trades_table['entry_time'].sort_values()
    start = entries.iloc[0].normalize()
    end = entries.iloc[-1].normalize()
    train = pd.Timedelta(days=train_days)
    test = pd.Timedelta(days=test_days)
    step = pd.Timedelta(days=step_days)
    end_exclusive = end + pd.Timedelta(days=1)

    oos_pfs = []
    cur = start
    while cur + train + test <= end_exclusive:
        test_start = cur + train
        test_end = test_start + test
        oos_trades = trades_table[(trades_table['entry_time'] >= test_start) & (trades_table['entry_time'] < test_end)]
        stats = compute_trade_stats(oos_trades)
        pf = stats['profit_factor']
        if pd.notna(pf):
            oos_pfs.append(float(pf))
        cur += step

    if not oos_pfs:
        return None

    median_pf = float(pd.Series(oos_pfs).median())
    below = sum(1 for pf in oos_pfs if pf < float(pf_min))
    return {
        'folds': len(oos_pfs),
        'median_pf': median_pf,
        'min_pf': min(oos_pfs),
        'pct_below_min': (below / len(oos_pfs)) * 100.0,
    }


def walk_forward_oos_pf_pct(trades_table, train_pct, test_pct, step_pct, pf_min):
    if trades_table is None or trades_table.empty:
        return None
    if train_pct <= 0 or test_pct <= 0 or step_pct <= 0:
        return None

    entries = trades_table['entry_time'].sort_values()
    if entries.empty:
        return None
    start = entries.iloc[0].normalize()
    end = entries.iloc[-1].normalize()
    total_days = (end - start).days + 1
    if total_days < 2:
        return None

    train_days = max(1, int(total_days * train_pct))
    test_days = max(1, int(total_days * test_pct))
    step_days = max(1, int(total_days * step_pct))
    if train_days + test_days > total_days:
        return None

    stats = walk_forward_oos_pf(trades_table, train_days, test_days, step_days, pf_min)
    if stats is None:
        return None
    stats['train_days'] = train_days
    stats['test_days'] = test_days
    stats['step_days'] = step_days
    stats['total_days'] = total_days
    return stats


def _resolve_prop_value(pct_value, abs_value, base):
    if abs_value is not None:
        return float(abs_value)
    if pct_value is None:
        return None
    return float(base * (pct_value / 100.0))


def evaluate_prop_path(trades_table, start_balance, target, max_dd, max_dd_mode, daily_dd, daily_mode):
    if trades_table is None or trades_table.empty:
        return None
    if target is None or max_dd is None:
        return None

    time_col = 'exit_time' if 'exit_time' in trades_table.columns else 'entry_time'
    trades = trades_table.sort_values(time_col).reset_index(drop=True)
    equity = start_balance
    peak = start_balance
    day_start = start_balance
    day_peak = start_balance
    last_day = None

    for idx, row in enumerate(trades.itertuples(index=False), start=0):
        trade_time = getattr(row, time_col)
        trade_day = trade_time.normalize()
        if last_day is None or trade_day != last_day:
            day_start = equity
            day_peak = equity
            last_day = trade_day

        equity += row.pnl
        if max_dd_mode == 'trailing':
            if equity > peak:
                peak = equity
        if daily_dd is not None:
            if daily_mode == 'start':
                if (day_start - equity) >= daily_dd:
                    return {'result': 'FAIL_DAILY_DD', 'equity': equity}
            else:
                if equity > day_peak:
                    day_peak = equity
                if (day_peak - equity) >= daily_dd:
                    return {'result': 'FAIL_DAILY_DD', 'equity': equity}
        if max_dd_mode == 'trailing':
            if (peak - equity) >= max_dd:
                return {'result': 'FAIL_MAX_DD', 'equity': equity}
        else:
            if (start_balance - equity) >= max_dd:
                return {'result': 'FAIL_MAX_DD', 'equity': equity}
        if (equity - start_balance) >= target:
            return {
                'result': 'PASS_TARGET',
                'equity': equity,
                'pass_index': idx,
                'pass_time': trade_time,
            }

    return {'result': 'NO_TARGET', 'equity': equity}


def simulate_prop_mc(trades_table, start_balance, target, max_dd, max_dd_mode, daily_dd, daily_mode, iterations, seed, shuffle_by):
    if trades_table is None or trades_table.empty:
        return None
    if iterations < 1:
        return None
    if target is None or max_dd is None:
        return None

    time_col = 'exit_time' if 'exit_time' in trades_table.columns else 'entry_time'
    trades = trades_table.sort_values(time_col).reset_index(drop=True)
    pnls = trades['pnl'].tolist()
    days = trades[time_col].dt.normalize().tolist()
    trade_pairs = list(zip(days, pnls))
    rng = random.Random(seed)

    pass_count = 0
    fail_max_dd = 0
    fail_daily = 0
    no_target = 0

    for _ in range(iterations):
        if shuffle_by == 'day':
            day_map = {}
            for d, pnl in trade_pairs:
                day_map.setdefault(d, []).append(pnl)
            day_keys = list(day_map.keys())
            rng.shuffle(day_keys)
            sim_pnls = []
            sim_days = []
            for d in day_keys:
                for pnl in day_map[d]:
                    sim_pnls.append(pnl)
                    sim_days.append(d)
        else:
            shuffled_pairs = trade_pairs[:]
            rng.shuffle(shuffled_pairs)
            sim_days = [d for d, _ in shuffled_pairs] if daily_dd is not None else None
            sim_pnls = [p for _, p in shuffled_pairs]

        equity = start_balance
        peak = start_balance
        day_start = start_balance
        day_peak = start_balance
        last_day = None
        outcome = 'NO_TARGET'

        for i, pnl in enumerate(sim_pnls):
            if daily_dd is not None and sim_days is not None:
                trade_day = sim_days[i]
                if last_day is None or trade_day != last_day:
                    day_start = equity
                    day_peak = equity
                    last_day = trade_day

            equity += pnl
            if max_dd_mode == 'trailing':
                if equity > peak:
                    peak = equity
            if daily_dd is not None:
                if daily_mode == 'start':
                    if (day_start - equity) >= daily_dd:
                        outcome = 'FAIL_DAILY_DD'
                        break
                else:
                    if equity > day_peak:
                        day_peak = equity
                    if (day_peak - equity) >= daily_dd:
                        outcome = 'FAIL_DAILY_DD'
                        break
            if max_dd_mode == 'trailing':
                if (peak - equity) >= max_dd:
                    outcome = 'FAIL_MAX_DD'
                    break
            else:
                if (start_balance - equity) >= max_dd:
                    outcome = 'FAIL_MAX_DD'
                    break
            if (equity - start_balance) >= target:
                outcome = 'PASS_TARGET'
                break

        if outcome == 'PASS_TARGET':
            pass_count += 1
        elif outcome == 'FAIL_DAILY_DD':
            fail_daily += 1
        elif outcome == 'FAIL_MAX_DD':
            fail_max_dd += 1
        else:
            no_target += 1

    total = pass_count + fail_max_dd + fail_daily + no_target
    if total == 0:
        return None
    return {
        'pass_rate': (pass_count / total) * 100.0,
        'fail_max_dd_rate': (fail_max_dd / total) * 100.0,
        'fail_daily_rate': (fail_daily / total) * 100.0,
        'no_target_rate': (no_target / total) * 100.0,
        'iterations': total,
    }


def get_post_pass_trades(trades_table, pass_index):
    if trades_table is None or trades_table.empty or pass_index is None:
        return None
    time_col = 'exit_time' if 'exit_time' in trades_table.columns else 'entry_time'
    trades = trades_table.sort_values(time_col).reset_index(drop=True)
    if pass_index >= len(trades) - 1:
        return trades.iloc[0:0]
    return trades.iloc[pass_index + 1 :].copy()


def split_equity_is_oos(trades_table):
    """Chronologically split completed trades for visual IS/OOS validation."""
    if trades_table is None or trades_table.empty or 'entry_time' not in trades_table.columns:
        return None, None, None
    ordered = trades_table.sort_values('entry_time').reset_index(drop=True)
    if CURVE_SPLIT_DATE:
        split_ts = pd.to_datetime(CURVE_SPLIT_DATE)
    else:
        split_idx = int(len(ordered) * float(EQUITY_IS_OOS_TRAIN_PCT))
        if split_idx <= 0 or split_idx >= len(ordered):
            return None, None, None
        split_ts = pd.Timestamp(ordered.loc[split_idx, 'entry_time'])
    is_trades = ordered[ordered['entry_time'] < split_ts].copy()
    oos_trades = ordered[ordered['entry_time'] >= split_ts].copy()
    if is_trades.empty or oos_trades.empty:
        return None, None, None
    return split_ts, is_trades, oos_trades


def build_edge_guard_report(trades_table):
    split_ts, is_trades, oos_trades = split_equity_is_oos(trades_table)
    if split_ts is None:
        return {'verdict': 'INSUFFICIENT_DATA'}
    is_stats = compute_trade_stats(is_trades, starting_balance=STARTING_BALANCE)
    oos_start = STARTING_BALANCE + float(is_trades['pnl'].sum())
    oos_stats = compute_trade_stats(oos_trades, starting_balance=oos_start)
    flags = []
    if is_stats['total_trades'] < EDGE_GUARD_MIN_TRADES_PER_SEGMENT:
        flags.append('IS_SAMPLE')
    if oos_stats['total_trades'] < EDGE_GUARD_MIN_TRADES_PER_SEGMENT:
        flags.append('OOS_SAMPLE')
    if oos_stats['profit_factor'] < EDGE_GUARD_MIN_OOS_PF:
        flags.append('OOS_PF')
    if oos_stats['total_pnl'] <= 0:
        flags.append('OOS_PNL')
    if oos_stats['max_drawdown_pct'] > EDGE_GUARD_MAX_OOS_DD_PCT:
        flags.append('OOS_DD')
    return {
        'verdict': 'PASS' if not flags else 'REVIEW',
        'flags': flags,
        'split_ts': split_ts,
        'is_stats': is_stats,
        'oos_stats': oos_stats,
    }


def plot_balance_curve(trades_table):
    if trades_table is None or trades_table.empty:
        return
    if 'equity_before' in trades_table.columns and not trades_table['equity_before'].empty:
        start_balance = float(trades_table['equity_before'].iloc[0])
    else:
        start_balance = float(STARTING_BALANCE)
    equity = pd.concat(
        [pd.Series([start_balance], dtype='float64'), trades_table['equity_after'].reset_index(drop=True)],
        ignore_index=True,
    )
    fig, ax = plt.subplots(figsize=(12, 4))
    x_values = list(range(len(equity)))
    split_ts, is_trades, oos_trades = split_equity_is_oos(trades_table) if EQUITY_IS_OOS_ENABLED else (None, None, None)
    if split_ts is None:
        ax.plot(x_values, equity.values, color='blue', linewidth=1.5, label='Equity')
        ax.set_title('Balance Curve')
    else:
        boundary = len(is_trades)
        ax.plot(x_values[:boundary + 1], equity.values[:boundary + 1], color='#1f77b4', linewidth=1.6, label='In-Sample equity')
        ax.plot(x_values[boundary:], equity.values[boundary:], color='#2ca02c', linewidth=1.6, label='Out-of-Sample equity')
        ax.axvspan(boundary, len(equity) - 1, color='#2ca02c', alpha=EQUITY_IS_OOS_SHADE_ALPHA, label='OOS period')
        ax.axvline(boundary, color='#333333', linestyle='--', linewidth=1.1)
        ax.annotate(
            f'IS / OOS split\n{split_ts.date()}',
            xy=(boundary, equity.iloc[boundary]), xytext=(6, 8),
            textcoords='offset points', fontsize=8, color='#333333',
        )
        ax.set_title('Balance Curve — In-Sample / Out-of-Sample')
    ax.set_xlabel('Trades')
    ax.set_ylabel('Balance')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')
    fig.tight_layout()
    plt.show()
    plt.close(fig)


def plot_random_trades_matplotlib(data, trades_table, n=5, window=30):
    sample = trades_table.sample(n=min(n, len(trades_table)))
    range_end_inclusive = BAR_TIME_MODE == 'close'

    for idx, trade in enumerate(sample.to_dict('records'), 1):
        entry_idx = int(trade['entry_idx'])
        exit_idx = int(trade['exit_idx'])
        start = max(entry_idx - window, 0)
        end = min(exit_idx + window, len(data) - 1)
        view = data.iloc[start:end + 1]
        fig, ax = plt.subplots(figsize=(12, 6))
        plt.subplots_adjust(bottom=0.12)
        tv_bg = '#bfc2cb'
        bull_color = '#9bcfc8'
        bear_color = '#111319'
        wick_color = '#111319'
        axis_color = '#3e4250'
        grid_color = '#808590'
        line_colors = {
            'signal_start': '#1A9E9A',
            'trade_end': '#6E7B8B',
            'range_box': '#E08A2E',
            'entry_price': '#2F6FDB',
            'stop_loss': '#D95E5E',
            'take_profit': '#2F9E64',
            'range_high': '#B46A2C',
            'range_low': '#6E59CF',
            'break_even': '#B856C6',
            'entry_vline': '#3FA7D6',
            'exit_vline': '#4C4F69',
            'be_trigger_vline': '#C65F93',
            'be_move_vline': '#8F3F71',
            'cross_v': '#4B5563',
            'cross_h': '#6B7280',
        }
        fig.patch.set_facecolor(tv_bg)
        ax.set_facecolor(tv_bg)

        if len(view.index) > 1:
            bar_delta = view.index[1] - view.index[0]
        else:
            bar_delta = pd.Timedelta(minutes=5)

        min_x = -0.5
        right_pad_bars = max(int(window * 0.75), 20)
        max_x = (len(view) - 0.5) + right_pad_bars

        # Shade range windows on time axis (per day)
        if 'day' in view.columns:
            range_window_labeled = False
            session_lines_labeled = False
            for d in view['day'].unique():
                day_mask = view['day'] == d
                range_mask = day_mask & time_mask(
                    view['time'],
                    RANGE_START_TIME,
                    RANGE_END_TIME,
                    include_end=range_end_inclusive,
                )
                if range_mask.any():
                    idxs = [i for i, v in enumerate(range_mask) if v]
                    label = 'Range Window' if not range_window_labeled else None
                    ax.axvspan(
                        idxs[0] - 0.5,
                        idxs[-1] + 0.5,
                        color='gold',
                        alpha=0.12,
                        label=label,
                    )
                    range_window_labeled = True

                # Draw session markers (signal start and trade end) for this day if visible
                if BAR_TIME_MODE == 'open':
                    signal_mask = day_mask & (view['time'] >= RANGE_END_TIME)
                else:
                    signal_mask = day_mask & (view['time'] > RANGE_END_TIME)
                trade_end_mask = day_mask & (view['time'] >= TRADE_END_TIME)

                if signal_mask.any():
                    idxs = [i for i, v in enumerate(signal_mask) if v]
                    signal_x = idxs[0]
                    ax.axvline(
                        signal_x,
                        color=line_colors['signal_start'],
                        linestyle='--',
                        linewidth=1.2,
                        alpha=0.82,
                        label='Signal Start' if not session_lines_labeled else None,
                    )
                if trade_end_mask.any():
                    idxs = [i for i, v in enumerate(trade_end_mask) if v]
                    trade_end_x = idxs[0]
                    ax.axvline(
                        trade_end_x,
                        color=line_colors['trade_end'],
                        linestyle='--',
                        linewidth=1.2,
                        alpha=0.82,
                        label='Trade End' if not session_lines_labeled else None,
                    )
                if signal_mask.any() or trade_end_mask.any():
                    session_lines_labeled = True

            # Draw price range box for the trade day if visible
            trade_day = trade['entry_time'].normalize()
            day_mask = view['day'] == trade_day
            range_mask = day_mask & time_mask(
                view['time'],
                RANGE_START_TIME,
                RANGE_END_TIME,
                include_end=range_end_inclusive,
            )
            if range_mask.any():
                idxs = [i for i, v in enumerate(range_mask) if v]
                x0 = idxs[0] - 0.5
                width = (idxs[-1] - idxs[0]) + 1.0
                y0 = trade['range_low']
                height = trade['range_high'] - trade['range_low']
                ax.add_patch(
                    Rectangle(
                        (x0, y0),
                        width,
                        height,
                        fill=False,
                        edgecolor=line_colors['range_box'],
                        linewidth=1.7,
                        linestyle='--',
                        label='Range Box',
                    )
                )

        for x, (o, h, l, c) in enumerate(
            view[['open', 'high', 'low', 'close']].itertuples(index=False, name=None)
        ):
            color = bull_color if c >= o else bear_color
            ax.vlines(x, l, h, color=wick_color, linewidth=0.8)
            body_low = min(o, c)
            body_high = max(o, c)
            height = body_high - body_low
            ax.add_patch(
                Rectangle(
                    (x - 0.3, body_low),
                    0.6,
                    height if height > 0 else 0.000001,
                    color=color,
                    alpha=1.0,
                )
            )

        entry_x = entry_idx - start
        exit_x = exit_idx - start
        ax.scatter([entry_x], [trade['entry_price']], color=line_colors['entry_vline'], marker='^', s=80, label='Entry')
        ax.scatter([exit_x], [trade['exit_price']], color=line_colors['exit_vline'], marker='v', s=80, label='Exit')
        ax.axhline(
            trade['entry_price'],
            color=line_colors['entry_price'],
            linestyle='--',
            linewidth=1.6,
            alpha=0.92,
            label='Entry Price',
        )
        ax.axhline(
            trade['stop_loss'],
            color=line_colors['stop_loss'],
            linestyle='--',
            linewidth=1.5,
            alpha=0.9,
            label='Stop Loss',
        )
        ax.axhline(
            trade['take_profit'],
            color=line_colors['take_profit'],
            linestyle='--',
            linewidth=1.5,
            alpha=0.9,
            label='Take Profit',
        )
        ax.axhline(
            trade['range_high'],
            color=line_colors['range_high'],
            linestyle='-.',
            linewidth=1.7,
            alpha=0.9,
            label='Range High',
        )
        ax.axhline(
            trade['range_low'],
            color=line_colors['range_low'],
            linestyle='-.',
            linewidth=1.7,
            alpha=0.9,
            label='Range Low',
        )
        be_price = None
        if trade.get('be_moved', False) and trade.get('be_stop') is not None:
            be_price = trade['be_stop']
            ax.axhline(
                be_price,
                color=line_colors['break_even'],
                linestyle=':',
                linewidth=1.5,
                alpha=0.9,
                label='Break Even',
            )
        elif trade.get('be_triggered', False):
            be_price = trade['entry_price']
            ax.axhline(
                be_price,
                color=line_colors['break_even'],
                linestyle=':',
                linewidth=1.5,
                alpha=0.9,
                label='Break Even',
            )
        ax.axvline(entry_x, color=line_colors['entry_vline'], linestyle=':', linewidth=1.4, alpha=0.9)
        ax.axvline(exit_x, color=line_colors['exit_vline'], linestyle=':', linewidth=1.4, alpha=0.9)
        if trade.get('be_triggered', False) and trade.get('be_trigger_idx') is not None:
            be_trigger_i = int(trade['be_trigger_idx']) - start
            if 0 <= be_trigger_i < len(view):
                ax.axvline(
                    be_trigger_i,
                    color=line_colors['be_trigger_vline'],
                    linestyle='--',
                    linewidth=1.4,
                    alpha=0.9,
                    label='BE Trigger',
                )
        if trade.get('be_moved', False) and trade.get('be_move_idx') is not None:
            be_i = int(trade['be_move_idx']) - start
            if 0 <= be_i < len(view):
                ax.axvline(
                    be_i,
                    color=line_colors['be_move_vline'],
                    linestyle=':',
                    linewidth=1.4,
                    alpha=0.9,
                    label='BE Move',
                )
                if be_price is None:
                    be_price = trade.get('be_stop', trade['entry_price'])
                ax.scatter([be_i], [be_price], color=line_colors['break_even'], s=50, marker='o', label='BE Point')
                ax.annotate(
                    f"BE {view.index[be_i].strftime('%H:%M')}",
                    xy=(be_i, be_price),
                    xytext=(6, 6),
                    textcoords='offset points',
                    fontsize=8,
                    color=line_colors['break_even'],
                )

        title_date = trade['entry_time'].date()
        ax.set_title(
            f"{trade['direction']} | {title_date} | Entry {trade['entry_time']} -> "
            f"Exit {trade['exit_time']} | {trade['exit_reason']}"
        )
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position('right')
        ax.set_xlabel('')
        ax.set_ylabel('')
        ax.tick_params(axis='x', colors=axis_color, length=0)
        ax.tick_params(axis='y', colors=axis_color, length=0)
        ax.spines['left'].set_visible(False)
        ax.spines['top'].set_visible(False)
        ax.spines['bottom'].set_color('#8f94a0')
        ax.spines['right'].set_color('#8f94a0')
        ax.xaxis.set_major_locator(MaxNLocator(nbins=12, integer=True))

        def format_time_label(x, _pos):
            if len(view) == 0:
                return ''
            i = int(round(x))
            if i < 0:
                ts = view.index[0] + (i * bar_delta)
            elif i < len(view):
                ts = view.index[i]
            else:
                ts = view.index[-1] + ((i - (len(view) - 1)) * bar_delta)
            return ts.strftime('%H:%M')

        ax.xaxis.set_major_formatter(FuncFormatter(format_time_label))
        ax.grid(True, alpha=0.18, axis='x', color=grid_color, linewidth=0.6)
        ax.legend(loc='upper left', ncol=2, fontsize=8)

        total_span = max_x - min_x
        min_span = max(12.0, min(float(len(view)), 60.0))
        init_zoom = max(float(RANDOM_TRADE_X_STRETCH), 1e-6)
        init_span = len(view) / init_zoom
        init_span = max(min_span, min(init_span, total_span))
        trade_center = (entry_x + exit_x) / 2.0

        def clamp_xlim(left, right):
            span = right - left
            span = max(min_span, min(span, total_span))
            left = max(min_x, min(left, max_x - span))
            right = left + span
            return left, right

        def centered_xlim(center, span):
            left = center - (span / 2.0)
            right = center + (span / 2.0)
            return clamp_xlim(left, right)

        left0, right0 = centered_xlim(trade_center, init_span)
        ax.set_xlim(left0, right0)
        cross_v = ax.axvline(
            float('nan'),
            color=line_colors['cross_v'],
            linewidth=0.9,
            alpha=0.5,
            linestyle='--',
            zorder=0,
        )
        cross_h = ax.axhline(
            float('nan'),
            color=line_colors['cross_h'],
            linewidth=0.9,
            alpha=0.5,
            linestyle='--',
            zorder=0,
        )
        cross_v.set_visible(False)
        cross_h.set_visible(False)
        ohlc_text = ax.text(
            0.01,
            1.005,
            '',
            transform=ax.transAxes,
            fontsize=9,
            color='#1f2330',
            ha='left',
            va='bottom',
        )

        # Hover tooltip with time and OHLC
        annot = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w", alpha=0.9),
            arrowprops=dict(arrowstyle="->"),
        )
        annot.set_visible(False)

        def update_ohlc_panel(i):
            if len(view) == 0:
                ohlc_text.set_text('')
                return
            i = max(0, min(int(i), len(view) - 1))
            r = view.iloc[i]
            d = r['close'] - r['open']
            pct = (d / r['open'] * 100.0) if r['open'] else 0.0
            sign = '+' if d >= 0 else ''
            ohlc_text.set_text(
                f"{view.index[i].strftime('%Y-%m-%d %H:%M')}  "
                f"O {r['open']:.1f}  H {r['high']:.1f}  L {r['low']:.1f}  C {r['close']:.1f}  "
                f"{sign}{d:.1f} ({sign}{pct:.2f}%)"
            )

        update_ohlc_panel(len(view) - 1)

        interaction = {
            'mode': None,
            'press_x': None,
            'press_y': None,
            'press_px': None,
            'orig_xlim': None,
            'orig_ylim': None,
            'anchor_x': None,
        }

        def in_bottom_scale(event):
            if event.x is None or event.y is None:
                return False
            bbox = ax.get_window_extent()
            within_x = bbox.x0 <= event.x <= bbox.x1
            near_bottom = (bbox.y0 - 18.0) <= event.y <= (bbox.y0 + 18.0)
            return within_x and near_bottom

        def in_right_scale(event):
            if event.x is None or event.y is None:
                return False
            bbox = ax.get_window_extent()
            within_y = bbox.y0 <= event.y <= bbox.y1
            near_right = (bbox.x1 - 18.0) <= event.x <= (bbox.x1 + 42.0)
            return within_y and near_right

        def on_press(event):
            if event.button != 1:
                return
            x0, x1 = ax.get_xlim()
            if in_right_scale(event):
                y0, y1 = ax.get_ylim()
                interaction['mode'] = 'yzoom'
                interaction['press_px'] = event.y
                interaction['orig_ylim'] = (y0, y1)
            elif in_bottom_scale(event):
                interaction['mode'] = 'xzoom'
                interaction['press_px'] = event.x
                interaction['orig_xlim'] = (x0, x1)
                if event.inaxes == ax and event.xdata is not None:
                    interaction['anchor_x'] = event.xdata
                else:
                    interaction['anchor_x'] = (x0 + x1) / 2.0
            elif event.inaxes == ax and event.xdata is not None:
                interaction['mode'] = 'pan'
                interaction['press_x'] = event.xdata
                interaction['press_y'] = event.ydata
                interaction['orig_xlim'] = (x0, x1)
                interaction['orig_ylim'] = ax.get_ylim()
            if interaction['mode'] is not None and annot.get_visible():
                annot.set_visible(False)
                cross_v.set_visible(False)
                cross_h.set_visible(False)
                fig.canvas.draw_idle()

        def on_motion(event):
            mode = interaction.get('mode')
            if mode == 'pan':
                if event.inaxes != ax or event.xdata is None or event.ydata is None:
                    return
                orig = interaction.get('orig_xlim')
                orig_y = interaction.get('orig_ylim')
                press_x = interaction.get('press_x')
                press_y = interaction.get('press_y')
                if orig is None or orig_y is None or press_x is None or press_y is None:
                    return
                dx = event.xdata - press_x
                left, right = clamp_xlim(orig[0] - dx, orig[1] - dx)
                ax.set_xlim(left, right)
                dy = event.ydata - press_y
                ax.set_ylim(orig_y[0] - dy, orig_y[1] - dy)
                fig.canvas.draw_idle()
                return

            if mode == 'xzoom':
                if event.x is None:
                    return
                orig = interaction.get('orig_xlim')
                press_px = interaction.get('press_px')
                if orig is None or press_px is None:
                    return
                drag_px = event.x - press_px
                zoom_factor = 1.0 - (drag_px / 300.0)
                zoom_factor = max(0.2, min(4.0, zoom_factor))
                orig_span = orig[1] - orig[0]
                new_span = orig_span * zoom_factor
                anchor = interaction.get('anchor_x')
                if anchor is None:
                    anchor = (orig[0] + orig[1]) / 2.0
                left, right = centered_xlim(anchor, new_span)
                ax.set_xlim(left, right)
                fig.canvas.draw_idle()
                return

            if mode == 'yzoom':
                if event.y is None:
                    return
                orig = interaction.get('orig_ylim')
                press_px = interaction.get('press_px')
                if orig is None or press_px is None:
                    return
                drag_py = event.y - press_px
                zoom_factor = 1.0 - (drag_py / 300.0)
                zoom_factor = max(0.2, min(4.0, zoom_factor))
                y_center = (orig[0] + orig[1]) / 2.0
                y_span = (orig[1] - orig[0]) * zoom_factor
                y_span = max(y_span, 1e-6)
                ax.set_ylim(y_center - (y_span / 2.0), y_center + (y_span / 2.0))
                fig.canvas.draw_idle()
                return

            if event.inaxes != ax or event.xdata is None:
                if annot.get_visible():
                    annot.set_visible(False)
                    cross_v.set_visible(False)
                    cross_h.set_visible(False)
                    fig.canvas.draw_idle()
                return
            x = int(round(event.xdata))
            if x < 0 or x >= len(view):
                if annot.get_visible():
                    annot.set_visible(False)
                    cross_v.set_visible(False)
                    cross_h.set_visible(False)
                    fig.canvas.draw_idle()
                return
            row = view.iloc[x]
            annot.xy = (x, row['close'])
            annot.set_text(
                f"{view.index[x].strftime('%H:%M')}\n"
                f"O:{row['open']:.2f} H:{row['high']:.2f}\n"
                f"L:{row['low']:.2f} C:{row['close']:.2f}"
            )
            update_ohlc_panel(x)
            cross_v.set_xdata([x, x])
            cross_h.set_ydata([row['close'], row['close']])
            cross_v.set_visible(True)
            cross_h.set_visible(True)
            annot.set_visible(True)
            fig.canvas.draw_idle()

        def on_release(_event):
            interaction['mode'] = None
            interaction['press_x'] = None
            interaction['press_y'] = None
            interaction['press_px'] = None
            interaction['orig_xlim'] = None
            interaction['orig_ylim'] = None
            interaction['anchor_x'] = None

        def on_scroll(event):
            if event.inaxes != ax:
                return
            x0, x1 = ax.get_xlim()
            current_span = x1 - x0
            if current_span <= 0:
                return
            step = getattr(event, 'step', 0)
            if step == 0:
                button_name = str(getattr(event, 'button', '')).lower()
                if button_name == 'up':
                    step = 1
                elif button_name == 'down':
                    step = -1
            if step == 0:
                return
            factor = 0.85 if step > 0 else 1.18
            new_span = current_span * factor
            anchor = event.xdata if event.xdata is not None else ((x0 + x1) / 2.0)
            anchor_ratio = (anchor - x0) / current_span
            left = anchor - (anchor_ratio * new_span)
            right = left + new_span
            left, right = clamp_xlim(left, right)
            ax.set_xlim(left, right)
            fig.canvas.draw_idle()

        fig.canvas.mpl_connect("button_press_event", on_press)
        fig.canvas.mpl_connect("motion_notify_event", on_motion)
        fig.canvas.mpl_connect("button_release_event", on_release)
        fig.canvas.mpl_connect("scroll_event", on_scroll)
        plt.show()
        plt.close(fig)


def plot_random_trades_lightweight(data, trades_table, n=5, window=30, silent_fail=False):
    try:
        from lightweight_charts import Chart
    except Exception as exc:
        if not silent_fail:
            print(f"[trade-plot] lightweight backend import failed: {exc}")
        return False

    sample = trades_table.sample(n=min(n, len(trades_table)))
    range_end_inclusive = BAR_TIME_MODE == 'close'

    def infer_pip_factor_from_prices(price_series):
        decimals = 0
        checked = 0
        for value in price_series:
            if pd.isna(value):
                continue
            try:
                numeric = float(value)
            except Exception:
                continue
            text = f"{numeric:.8f}".rstrip('0').rstrip('.')
            if '.' in text:
                decimals = max(decimals, len(text.split('.')[-1]))
            checked += 1
            if checked >= 250:
                break
        decimals = max(0, min(decimals, 4))
        return float(10 ** decimals) if decimals > 0 else 1.0

    def attach_price_range_tool(chart, pip_factor=10.0):
        safe_chart_id = str(chart.id).replace('.', '_')
        safe_pip_factor = float(pip_factor)
        chart.run_script(
            f"""
            (function() {{
                try {{
                    var handler = {chart.id};
                    if (!handler || !handler.toolBox || !handler.toolBox.div) return;
                    var toolBox = handler.toolBox;
                    if (toolBox._priceRangePatchInstalled) return;
                    toolBox._priceRangePatchInstalled = true;

                    function isFiniteNumber(value) {{
                        return (typeof value === 'number') && isFinite(value);
                    }}

                    var boxMeta = (toolBox.buttons && toolBox.buttons.length >= 4) ? toolBox.buttons[3] : null;
                    var boxButton = boxMeta && boxMeta.div ? boxMeta.div : null;
                    if (!boxButton) return;

                    boxButton.title = 'Price Range (Alt+P)';
                    boxButton.innerHTML = '';
                    var prLabel = document.createElement('span');
                    prLabel.textContent = 'PR';
                    prLabel.style.display = 'block';
                    prLabel.style.width = '29px';
                    prLabel.style.height = '29px';
                    prLabel.style.lineHeight = '29px';
                    prLabel.style.textAlign = 'center';
                    prLabel.style.color = '#d8d9db';
                    prLabel.style.fontSize = '10px';
                    prLabel.style.fontWeight = '700';
                    prLabel.style.letterSpacing = '0.4px';
                    boxButton.appendChild(prLabel);

                    var status = document.createElement('div');
                    status.id = '{safe_chart_id}_price_range_status';
                    status.style.position = 'absolute';
                    status.style.right = '12px';
                    status.style.top = '12px';
                    status.style.zIndex = '2100';
                    status.style.padding = '4px 8px';
                    status.style.fontSize = '11px';
                    status.style.border = '1px solid rgba(46, 54, 70, 0.45)';
                    status.style.borderRadius = '6px';
                    status.style.background = 'rgba(220, 224, 233, 0.82)';
                    status.style.color = '#1f2330';
                    status.style.pointerEvents = 'none';
                    status.style.display = 'none';
                    handler.div.appendChild(status);

                    function findLatestBoxDrawing() {{
                        var drawings = null;
                        if (toolBox && toolBox._drawingTool && Array.isArray(toolBox._drawingTool.drawings)) {{
                            drawings = toolBox._drawingTool.drawings;
                        }}
                        if (!drawings || drawings.length === 0) return null;
                        for (var i = drawings.length - 1; i >= 0; i -= 1) {{
                            var drawing = drawings[i];
                            if (drawing && drawing._type === 'Box' && Array.isArray(drawing.points) && drawing.points.length >= 2) {{
                                return drawing;
                            }}
                        }}
                        return null;
                    }}

                    function pointX(point) {{
                        if (!point) return NaN;
                        var x = NaN;
                        try {{
                            if (point.time !== undefined && point.time !== null) {{
                                x = handler.chart.timeScale().timeToCoordinate(point.time);
                            }}
                            if (!isFiniteNumber(x) && point.logical !== undefined && point.logical !== null) {{
                                x = handler.chart.timeScale().logicalToCoordinate(point.logical);
                            }}
                        }} catch (_xErr) {{}}
                        return Number(x);
                    }}

                    function pointY(point) {{
                        if (!point) return NaN;
                        var y = NaN;
                        try {{
                            y = handler.series.priceToCoordinate(point.price);
                        }} catch (_yErr) {{}}
                        return Number(y);
                    }}

                    function formatValue(value, decimals) {{
                        return isFiniteNumber(value) ? value.toFixed(decimals) : 'n/a';
                    }}

                    function updatePriceRangeLabel() {{
                        try {{
                            var box = findLatestBoxDrawing();
                            if (!box) {{
                                status.style.display = 'none';
                                return;
                            }}

                            var p0 = Number(box.points[0] && box.points[0].price);
                            var p1 = Number(box.points[1] && box.points[1].price);
                            if (!isFiniteNumber(p0) || !isFiniteNumber(p1)) {{
                                status.style.display = 'none';
                                return;
                            }}

                            var delta = p1 - p0;
                            var absDelta = Math.abs(delta);
                            var low = Math.min(p0, p1);
                            var pct = low !== 0 ? (delta / Math.abs(low)) * 100 : NaN;
                            var pips = absDelta * {safe_pip_factor};

                            var l0 = Number(box.points[0] && box.points[0].logical);
                            var l1 = Number(box.points[1] && box.points[1].logical);
                            var bars = (isFiniteNumber(l0) && isFiniteNumber(l1))
                                ? (Math.abs(Math.round(l1 - l0)) + 1)
                                : NaN;

                            var sign = delta > 0 ? '+' : (delta < 0 ? '-' : '');
                            status.textContent =
                                sign + formatValue(absDelta, 1) +
                                ' (' + formatValue(pct, 2) + '%)' +
                                '  ' + Math.round(pips) + 'p' +
                                '  ' + (isFiniteNumber(bars) ? bars : 'n/a') + ' bars';

                            var x0 = pointX(box.points[0]);
                            var x1 = pointX(box.points[1]);
                            var y0 = pointY(box.points[0]);
                            var y1 = pointY(box.points[1]);
                            if (isFiniteNumber(x0) && isFiniteNumber(x1) && isFiniteNumber(y0) && isFiniteNumber(y1)) {{
                                status.style.left = (Math.max(x0, x1) + 8) + 'px';
                                status.style.top = ((y0 + y1) / 2) + 'px';
                                status.style.right = '';
                                status.style.transform = 'translateY(-50%)';
                            }} else {{
                                status.style.left = '';
                                status.style.right = '12px';
                                status.style.top = '12px';
                                status.style.transform = '';
                            }}
                            status.style.display = 'block';
                        }} catch (_labelErr) {{
                            status.style.display = 'none';
                        }}
                    }}

                    if (!Array.isArray(handler.commandFunctions)) {{
                        handler.commandFunctions = [];
                    }}
                    handler.commandFunctions.unshift(function(event) {{
                        try {{
                            if (handler.id !== window.handlerInFocus) return false;
                            var keyP = (event.code === 'KeyP') || (event.key === 'p') || (event.key === 'P');
                            if (event.altKey && keyP) {{
                                event.preventDefault();
                                if (boxButton && typeof boxButton.click === 'function') boxButton.click();
                                return true;
                            }}
                        }} catch (_hotkeyErr) {{}}
                        return false;
                    }});

                    if (typeof toolBox.removeActiveAndSave === 'function') {{
                        var originalRemoveActiveAndSave = toolBox.removeActiveAndSave.bind(toolBox);
                        toolBox.removeActiveAndSave = function() {{
                            try {{
                                originalRemoveActiveAndSave();
                            }} finally {{
                                updatePriceRangeLabel();
                            }}
                        }};
                    }}

                    try {{
                        handler.chart.timeScale().subscribeVisibleLogicalRangeChange(function() {{
                            updatePriceRangeLabel();
                        }});
                    }} catch (_subErr) {{}}
                    setInterval(updatePriceRangeLabel, 500);
                    updatePriceRangeLabel();
                }} catch (_priceRangeInitErr) {{}}
            }})();
            """
        )

    for idx, trade in enumerate(sample.to_dict('records'), 1):
        entry_idx = int(trade['entry_idx'])
        exit_idx = int(trade['exit_idx'])
        start = max(entry_idx - window, 0)
        end = min(exit_idx + window, len(data) - 1)
        view = data.iloc[start:end + 1]
        if view.empty:
            continue

        chart_df = view[['open', 'high', 'low', 'close']].copy()
        time_values = pd.to_datetime(view.index)
        if getattr(time_values, 'tz', None) is not None:
            time_values = time_values.tz_convert(None)
        chart_df.insert(0, 'time', time_values)

        title_date = trade['entry_time'].date()
        title = (
            f"Trade {idx} | {trade['direction']} | {title_date} | "
            f"Entry {trade['entry_time']} -> Exit {trade['exit_time']} | {trade['exit_reason']}"
        )

        chart_toolbox_enabled = bool(TRADE_PLOT_TOOLBOX_ENABLED or TRADE_PLOT_MEASURE_TOOL_ENABLED)
        chart = Chart(
            width=int(TRADE_PLOT_LIGHTWEIGHT_WIDTH),
            height=int(TRADE_PLOT_LIGHTWEIGHT_HEIGHT),
            title=title,
            toolbox=chart_toolbox_enabled,
        )
        lw_line_colors = {
            'r_start': 'rgba(217, 162, 43, 0.80)',
            'r_end': 'rgba(131, 168, 68, 0.80)',
            'signal': 'rgba(26, 158, 154, 0.82)',
            'session_end': 'rgba(110, 123, 139, 0.82)',
            'entry_vline': 'rgba(63, 167, 214, 0.86)',
            'exit_vline': 'rgba(76, 79, 105, 0.86)',
            'entry_price': 'rgba(47, 111, 219, 0.98)',
            'stop_loss': 'rgba(217, 94, 94, 0.95)',
            'take_profit': 'rgba(47, 158, 100, 0.95)',
            'range_high': 'rgba(180, 106, 44, 0.92)',
            'range_low': 'rgba(110, 89, 207, 0.92)',
            'break_even': 'rgba(184, 86, 198, 0.95)',
            'range_box': 'rgba(224, 138, 46, 0.92)',
            'range_fill': 'rgba(247, 209, 130, 0.14)',
            'cross_v': 'rgba(74, 80, 92, 0.68)',
            'cross_h': 'rgba(103, 114, 130, 0.68)',
        }
        chart.layout(background_color='#bfc2cb', text_color='#1f2330', font_size=12, font_family='Segoe UI')
        chart.grid(vert_enabled=True, horz_enabled=True, color='rgba(80, 86, 98, 0.25)', style='dotted')
        chart.candle_style(
            up_color='rgba(155, 207, 200, 1)',
            down_color='rgba(17, 19, 25, 1)',
            wick_visible=True,
            border_visible=True,
            border_up_color='rgba(17, 19, 25, 1)',
            border_down_color='rgba(17, 19, 25, 1)',
            wick_up_color='rgba(17, 19, 25, 1)',
            wick_down_color='rgba(17, 19, 25, 1)',
        )
        chart.crosshair(
            mode='normal',
            vert_visible=True,
            horz_visible=True,
            vert_color=lw_line_colors['cross_v'],
            horz_color=lw_line_colors['cross_h'],
            vert_style='dashed',
            horz_style='dashed',
            vert_label_background_color='rgb(55, 58, 66)',
            horz_label_background_color='rgb(55, 58, 66)',
        )
        chart.legend(
            visible=True,
            ohlc=True,
            percent=True,
            lines=True,
            color='rgb(31, 35, 48)',
            font_size=11,
            font_family='Segoe UI',
            color_based_on_candle=True,
        )
        chart.price_scale(
            auto_scale=True,
            border_visible=True,
            border_color='rgba(110, 115, 126, 0.8)',
            text_color='rgba(31, 35, 48, 0.95)',
            ticks_visible=True,
        )
        right_pad_bars = max(int(window * 0.75), 20)
        min_spacing = max(0.5, float(RANDOM_TRADE_X_STRETCH))
        chart.time_scale(
            right_offset=right_pad_bars,
            min_bar_spacing=min_spacing,
            visible=True,
            time_visible=True,
            seconds_visible=False,
            border_visible=True,
            border_color='rgba(110, 115, 126, 0.8)',
        )

        chart.set(chart_df)

        if len(chart_df) > 1:
            bar_delta = chart_df['time'].iloc[1] - chart_df['time'].iloc[0]
        else:
            bar_delta = pd.Timedelta(minutes=5)
        visible_end = chart_df['time'].iloc[-1] + (bar_delta * right_pad_bars)
        chart.set_visible_range(chart_df['time'].iloc[0], visible_end)

        def add_level_line(level_price, name, color, style='dashed', width=1):
            level_price = float(level_price)
            chart.horizontal_line(
                level_price,
                color=color,
                width=width,
                style=style,
                text=name,
                axis_label_visible=True,
            )

        if 'day' in view.columns:
            for d in view['day'].unique():
                day_mask = view['day'] == d
                range_mask = day_mask & time_mask(
                    view['time'],
                    RANGE_START_TIME,
                    RANGE_END_TIME,
                    include_end=range_end_inclusive,
                )
                if range_mask.any():
                    times = view.index[range_mask]
                    chart.vertical_line(
                        time=times[0],
                        color=lw_line_colors['r_start'],
                        width=1,
                        style='dotted',
                        text='R start',
                    )
                    chart.vertical_line(
                        time=times[-1],
                        color=lw_line_colors['r_end'],
                        width=1,
                        style='dotted',
                        text='R end',
                    )

                if BAR_TIME_MODE == 'open':
                    signal_mask = day_mask & (view['time'] >= RANGE_END_TIME)
                else:
                    signal_mask = day_mask & (view['time'] > RANGE_END_TIME)
                trade_end_mask = day_mask & (view['time'] >= TRADE_END_TIME)

                if signal_mask.any():
                    signal_time = view.index[signal_mask][0]
                    chart.vertical_line(
                        time=signal_time,
                        color=lw_line_colors['signal'],
                        width=2,
                        style='dashed',
                        text='Signal',
                    )
                if trade_end_mask.any():
                    trade_end_time = view.index[trade_end_mask][0]
                    chart.vertical_line(
                        time=trade_end_time,
                        color=lw_line_colors['session_end'],
                        width=2,
                        style='dashed',
                        text='End',
                    )

            trade_day = pd.to_datetime(trade['entry_time'])
            if getattr(trade_day, 'tzinfo', None) is not None:
                trade_day = trade_day.tz_convert(None)
            trade_day = trade_day.normalize()
            trade_day_mask = view['day'] == trade_day
            trade_day_range_mask = trade_day_mask & time_mask(
                view['time'],
                RANGE_START_TIME,
                RANGE_END_TIME,
                include_end=range_end_inclusive,
            )
            if trade_day_range_mask.any():
                range_times = view.index[trade_day_range_mask]
                chart.box(
                    start_time=range_times[0],
                    start_value=float(trade['range_low']),
                    end_time=range_times[-1],
                    end_value=float(trade['range_high']),
                    color=lw_line_colors['range_box'],
                    fill_color=lw_line_colors['range_fill'],
                    width=1,
                    style='dashed',
                )
                chart.marker(
                    time=range_times[-1],
                    position='above',
                    shape='square',
                    color='#222222',
                    text=f"Range High {float(trade['range_high']):.1f}",
                )
                chart.marker(
                    time=range_times[-1],
                    position='below',
                    shape='square',
                    color='#222222',
                    text=f"Range Low {float(trade['range_low']):.1f}",
                )

        is_long = str(trade.get('direction', '')).lower().startswith('long')
        entry_time = pd.to_datetime(trade['entry_time'])
        exit_time = pd.to_datetime(trade['exit_time'])
        if getattr(entry_time, 'tzinfo', None) is not None:
            entry_time = entry_time.tz_convert(None)
        if getattr(exit_time, 'tzinfo', None) is not None:
            exit_time = exit_time.tz_convert(None)
        entry_i_local = int(trade['entry_idx']) - start
        exit_i_local = int(trade['exit_idx']) - start
        if 0 <= entry_i_local < len(view):
            entry_time = pd.to_datetime(view.index[entry_i_local])
        if 0 <= exit_i_local < len(view):
            exit_time = pd.to_datetime(view.index[exit_i_local])

        chart.vertical_line(
            time=entry_time,
            color=lw_line_colors['entry_vline'],
            width=2,
            style='dashed',
            text='Entry',
        )
        chart.vertical_line(
            time=exit_time,
            color=lw_line_colors['exit_vline'],
            width=2,
            style='dashed',
            text='Exit',
        )

        add_level_line(trade['entry_price'], 'Entry', lw_line_colors['entry_price'], style='dashed', width=2)
        add_level_line(trade['stop_loss'], 'SL', lw_line_colors['stop_loss'], style='dashed', width=2)
        add_level_line(trade['take_profit'], 'TP', lw_line_colors['take_profit'], style='dashed', width=2)
        add_level_line(trade['range_high'], 'Range High', lw_line_colors['range_high'], style='solid', width=2)
        add_level_line(trade['range_low'], 'Range Low', lw_line_colors['range_low'], style='solid', width=2)

        if trade.get('be_moved', False) and trade.get('be_stop') is not None:
            add_level_line(trade['be_stop'], 'BE', lw_line_colors['break_even'], style='dotted', width=2)
        elif trade.get('be_triggered', False):
            add_level_line(trade['entry_price'], 'BE', lw_line_colors['break_even'], style='dotted', width=2)

        chart.marker(
            time=entry_time,
            position='below' if is_long else 'above',
            shape='arrow_up' if is_long else 'arrow_down',
            color='#2962FF',
            text='Entry',
        )
        chart.marker(
            time=exit_time,
            position='above' if is_long else 'below',
            shape='circle',
            color='#111319',
            text=f"Exit {trade['exit_reason']}",
        )

        if trade.get('be_triggered', False) and trade.get('be_trigger_idx') is not None:
            be_trigger_i = int(trade['be_trigger_idx']) - start
            if 0 <= be_trigger_i < len(view):
                chart.marker(
                    time=view.index[be_trigger_i],
                    position='inside',
                    shape='square',
                    color='rgba(128, 0, 128, 0.9)',
                    text='BE trig',
                )
        if trade.get('be_moved', False) and trade.get('be_move_idx') is not None:
            be_move_i = int(trade['be_move_idx']) - start
            if 0 <= be_move_i < len(view):
                chart.marker(
                    time=view.index[be_move_i],
                    position='inside',
                    shape='square',
                    color='rgba(128, 0, 128, 0.9)',
                    text='BE move',
                )

        inferred_pip_factor = infer_pip_factor_from_prices(chart_df['close'].values)
        attach_price_range_tool(chart, pip_factor=inferred_pip_factor)
        if not silent_fail:
            print("[trade-plot] Price Range tool injected (Box icon -> PR).")

        chart.show(block=bool(TRADE_PLOT_LIGHTWEIGHT_BLOCKING))

    return True


def plot_random_trades(data, trades_table, n=5, window=30):
    backend = str(TRADE_PLOT_BACKEND).strip().lower()
    if backend == 'lightweight':
        try:
            used = plot_random_trades_lightweight(data, trades_table, n=n, window=window, silent_fail=True)
        except Exception as exc:
            print(f"[trade-plot] lightweight backend runtime error: {exc}")
            used = False
        if used:
            return
        print("[trade-plot] lightweight backend unavailable, matplotlib fallback used.")
    plot_random_trades_matplotlib(data, trades_table, n=n, window=window)


def run_backtest(
    data,
    silent=False,
    save_trades=False,
    show_plots=True,
    run_distribution=None,
    run_curve_check=None,
    run_window_robustness=None,
    data_cache=None,
    overrides=None,
):
    validate_config()
    if run_window_robustness is None:
        run_window_robustness = WINDOW_ROBUSTNESS_ENABLED and not silent

    if data is None and data_cache is None:
        if not silent:
            print("No data.")
        return None
    if data is not None and data.empty:
        if not silent:
            print("No data.")
        return None

    if data_cache is None:
        data_cache = get_data_cache(data, ema_filter_enabled=EMA_FILTER_ENABLED, ema_period=EMA_PERIOD)
    if data_cache is None:
        if not silent:
            print("No data.")
        return None

    index_values = data_cache['index_values']
    data_start = index_values.min()
    data_end = index_values.max()
    data_days = (data_end - data_start).days + 1
    data_years = (data_end - data_start).days / 365.25

    trades = []
    position = None
    pending = None
    last_day = None
    trades_today = 0
    signal_used = False
    skip_day = False
    range_high = None
    range_low = None
    current_equity = STARTING_BALANCE

    # Handle non-global overrides (keeps solo backtest globals intact)
    if overrides is None:
        overrides = {}
    local_pending_expiry = int(overrides.get('PENDING_EXPIRY_BARS', PENDING_EXPIRY_BARS))
    local_trade_end_hour = int(overrides.get('TRADE_END_HOUR', TRADE_END_HOUR))
    local_trade_end_minute = int(overrides.get('TRADE_END_MINUTE', TRADE_END_MINUTE))
    if local_pending_expiry < 1:
        raise ValueError("PENDING_EXPIRY_BARS override must be >= 1")
    if local_trade_end_hour < 0 or local_trade_end_hour > 23 or local_trade_end_minute < 0 or local_trade_end_minute > 59:
        raise ValueError("TRADE_END_HOUR/TRADE_END_MINUTE override must be a valid time")
    local_trade_end_time = pd.Timestamp(f"{local_trade_end_hour:02d}:{local_trade_end_minute:02d}:00").time()
    if local_trade_end_time <= RANGE_END_TIME:
        raise ValueError("TRADE_END_TIME override must be > RANGE_END_TIME")
    index_values = data_cache['index_values']
    open_values = data_cache['open_values']
    high_values = data_cache['high_values']
    low_values = data_cache['low_values']
    close_values = data_cache['close_values']
    ema_values = data_cache.get('ema_values')
    day_values = data_cache['day_values']
    time_values = data_cache['time_values']
    if EMA_FILTER_ENABLED and ema_values is None:
        ema_values = pd.Series(close_values, index=index_values).ewm(
            span=int(EMA_PERIOD),
            adjust=False,
        ).mean().to_numpy()
        data_cache['ema_values'] = ema_values
    # Build once, then consume in loop for O(1) bar-index lookup.
    news_plan = build_news_filter_plan(index_values)
    news_trigger_map = news_plan['trigger_map'] if news_plan['enabled'] else {}
    news_blocked_days = set()
    if news_plan['enabled'] and not silent:
        print(
            "News filter: enabled "
            f"(events={news_plan['events_selected']}, trigger_bars={news_plan['trigger_bars']}) "
            f"from {news_plan['calendar_path']}"
        )
        if news_plan.get('calendar_latest_utc') is not None:
            gap_days = news_plan.get('coverage_gap_days')
            if gap_days is None:
                print(f"News calendar latest UTC: {news_plan['calendar_latest_utc']}")
            else:
                print(
                    f"News calendar latest UTC: {news_plan['calendar_latest_utc']} "
                    f"(gap vs data_end: {gap_days:.2f} days)"
                )

    range_end_inclusive = BAR_TIME_MODE == 'close'
    if BAR_TIME_MODE == 'open':
        in_session_values = [RANGE_END_TIME <= t < local_trade_end_time for t in time_values]
        after_signal_start_values = [t >= RANGE_END_TIME for t in time_values]
    else:
        in_session_values = [RANGE_END_TIME < t < local_trade_end_time for t in time_values]
        after_signal_start_values = [t > RANGE_END_TIME for t in time_values]
    in_range_window_values = [
        in_time_window(t, RANGE_START_TIME, RANGE_END_TIME, include_end=range_end_inclusive)
        for t in time_values
    ]
    pending_cancel_counts = {
        'expired': 0,
        'day_change': 0,
        'data_end': 0,
        'session_window': 0,
        'opposite_breakout': 0,
        'daily_trade_limit': 0,
        'day_skip': 0,
        'weekday_block': 0,
        'news_window': 0,
        'risk_guard': 0,
    }
    pending_debug_counts = {
        'same_bar_touch_blocked': 0,
        'touch_checks': 0,
        'fills': 0,
    }
    breakeven_counts = {
        'triggered': 0,
        'moved': 0,
    }
    trailing_counts = {
        'activated': 0,
        'moved': 0,
    }
    weekday_map = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    blocked_weekdays = set(BLOCKED_WEEKDAYS)

    total_bars = len(index_values)
    progress_enabled = PROGRESS_ENABLED and not silent
    progress_every = max(PROGRESS_EVERY_N, 1)
    progress_start = time.time() if progress_enabled else None
    for i in range(total_bars):
        if progress_enabled:
            if i % progress_every == 0 or i == total_bars - 1:
                elapsed = time.time() - progress_start if progress_start else 0.0
                done = i + 1
                rate = elapsed / done if done > 0 else 0.0
                eta_min = (rate * (total_bars - done)) / 60.0 if rate > 0 else 0.0
                pct = (done / total_bars) * 100.0 if total_bars else 100.0
                print(f"\rProgress: {done}/{total_bars} ({pct:.1f}%) ETA {eta_min:.1f}m", end="", flush=True)
        day = day_values[i]
        open_price = open_values[i]
        high_price = high_values[i]
        low_price = low_values[i]
        close_price = close_values[i]

        # Day change: reset day counters, cancel pending, and force-close carry positions if enabled
        if last_day is None or day != last_day:
            if pending is not None:
                pending_cancel_counts['day_change'] += 1
                pending = None
            if position is not None and CLOSE_POSITION_ON_DAY_CHANGE:
                day_exit_price = close_price if BAR_TIME_MODE == 'close' else open_price
                trade = close_trade(
                    position=position,
                    exit_time=index_values[i],
                    exit_idx=i,
                    exit_price=day_exit_price,
                    exit_reason='DAY_CHANGE',
                )
                trades.append(trade)
                current_equity += trade['pnl']
                position = None
            last_day = day
            trades_today = 0
            signal_used = False
            skip_day = False
            range_high = None
            range_low = None
            day_code = weekday_map[index_values[i].dayofweek]
            if day_code in blocked_weekdays:
                skip_day = True
                pending_cancel_counts['weekday_block'] += 1
            if day in news_blocked_days:
                skip_day = True

        blocked_days_now = news_trigger_map.get(i)
        if blocked_days_now:
            # When pre-event trigger fires:
            # - mark event day as blocked,
            # - optionally flatten position,
            # - cancel pending order to avoid entering just before event.
            news_blocked_days.update(blocked_days_now)
            if NEWS_FORCE_CLOSE_AND_SKIP_DAY and position is not None:
                trade = close_trade(
                    position=position,
                    exit_time=index_values[i],
                    exit_idx=i,
                    exit_price=open_price,
                    exit_reason='NEWS_PRE_EVENT',
                )
                trades.append(trade)
                current_equity += trade['pnl']
                position = None
            if pending is not None:
                pending_cancel_counts['news_window'] += 1
                pending = None
            if day in news_blocked_days:
                skip_day = True

        # Manage open position first
        if position is not None:
            if BREAKEVEN_ENABLED:
                triggered_now, moved_now = apply_breakeven(
                    position,
                    high_price,
                    low_price,
                    close_price,
                    i,
                    index_values[i],
                )
                if triggered_now:
                    breakeven_counts['triggered'] += 1
                if moved_now:
                    breakeven_counts['moved'] += 1

            exit_reason, exit_price = resolve_exit(
                direction=position['direction'],
                high=high_price,
                low=low_price,
                take_profit=position['take_profit'],
                stop_loss=position['stop_loss'],
            )

            in_session = in_session_values[i]
            if exit_reason is None:
                if BAR_TIME_MODE == 'close' and time_values[i] == local_trade_end_time:
                    exit_reason = 'EOD'
                    exit_price = close_price
                elif not in_session:
                    exit_reason = 'EOD'
                    exit_price = open_price

            if exit_reason is None and TRAILING_SL_ENABLED:
                trail_activated_now, trail_moved_now = update_trailing_stop_after_bar(
                    position,
                    high_price,
                    low_price,
                    close_price,
                    i,
                    index_values[i],
                )
                if trail_activated_now:
                    trailing_counts['activated'] += 1
                if trail_moved_now:
                    trailing_counts['moved'] += 1

            if exit_reason is not None:
                trade = close_trade(
                    position=position,
                    exit_time=index_values[i],
                    exit_idx=i,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                )
                trades.append(trade)
                current_equity += trade['pnl']
                position = None
            continue

        # Update range window running high/low (no look-ahead)
        if in_range_window_values[i]:
            if range_high is None:
                range_high = high_price
                range_low = low_price
            else:
                if high_price > range_high:
                    range_high = high_price
                if low_price < range_low:
                    range_low = low_price

        if skip_day:
            if pending is not None:
                pending_cancel_counts['day_skip'] += 1
                pending = None
            continue

        # Outside trade window: cancel pending and skip
        if not in_session_values[i]:
            if pending is not None:
                pending_cancel_counts['session_window'] += 1
                pending = None
            continue

        # Need range for the day
        if range_high is None or range_low is None:
            continue

        # Only after signal start
        if not after_signal_start_values[i]:
            continue

        # Max trades per day
        if trades_today >= MAX_TRADES_PER_DAY:
            if pending is not None:
                pending_cancel_counts['daily_trade_limit'] += 1
                pending = None
            continue

        if RISK_MODE == 'percent' and current_equity <= 0:
            if not silent:
                print("Equity <= 0, stopping.")
            break

        # Pending order check (expiry + fill)
        if pending is not None:
            cancel_pending = False
            if CANCEL_PENDING_ON_OPPOSITE_BREAKOUT:
                pending_range_high = pending['range_high']
                pending_range_low = pending['range_low']
                breakout_side = str(pending.get('breakout_side', pending.get('entry_side', ''))).upper()
                if breakout_side == 'LOW' and close_price > pending_range_high:
                    pending_cancel_counts['opposite_breakout'] += 1
                    cancel_pending = True
                elif breakout_side == 'HIGH' and close_price < pending_range_low:
                    pending_cancel_counts['opposite_breakout'] += 1
                    cancel_pending = True

            if cancel_pending:
                pending = None
            elif _pending_fill_bars_seen(pending['created_idx'], i) > local_pending_expiry:
                pending_cancel_counts['expired'] += 1
                pending = None
            else:
                direction = pending['direction']
                can_check_fill = ALLOW_SAME_BAR_FILL or i > pending['created_idx']
                if not can_check_fill:
                    if pending_touched(high_price, low_price, close_price, direction, pending['entry_price']):
                        pending_debug_counts['same_bar_touch_blocked'] += 1
                else:
                    touched = pending_touched(high_price, low_price, close_price, direction, pending['entry_price'])
                    if touched:
                        pending_debug_counts['touch_checks'] += 1
                    ema_value = ema_values[i] if ema_values is not None else pd.NA
                    ema_ok = ema_filter_passes(direction, pending['entry_price'], ema_value)
                    if touched and ema_ok:
                        position = pending.copy()
                        position['entry_time'] = index_values[i]
                        position['entry_idx'] = i
                        pending = None
                        trades_today += 1
                        pending_debug_counts['fills'] += 1
                        if BREAKEVEN_ENABLED:
                            triggered_now, moved_now = apply_breakeven(
                                position,
                                high_price,
                                low_price,
                                close_price,
                                i,
                                index_values[i],
                            )
                            if triggered_now:
                                breakeven_counts['triggered'] += 1
                            if moved_now:
                                breakeven_counts['moved'] += 1
                        # Resolve same-bar exit after fill to avoid optimistic bias
                        exit_reason, exit_price = resolve_exit_on_fill_bar(
                            position=position,
                            high=high_price,
                            low=low_price,
                        )
                        if exit_reason is not None:
                            trade = close_trade(
                                position=position,
                                exit_time=index_values[i],
                                exit_idx=i,
                                exit_price=exit_price,
                                exit_reason=exit_reason,
                            )
                            trades.append(trade)
                            current_equity += trade['pnl']
                            position = None
                            continue
                        if TRAILING_SL_ENABLED:
                            trail_activated_now, trail_moved_now = update_trailing_stop_after_bar(
                                position,
                                high_price,
                                low_price,
                                close_price,
                                i,
                                index_values[i],
                            )
                            if trail_activated_now:
                                trailing_counts['activated'] += 1
                            if trail_moved_now:
                                trailing_counts['moved'] += 1
                if position is not None:
                    continue
                if pending is not None:
                    continue

        # Breakout detection (close outside range)
        if ONE_SIGNAL_PER_DAY and signal_used:
            continue

        pending_created_now = False
        if STRATEGY_MODE == 'inverse':
            long_break = close_price < range_low
            short_break = close_price > range_high
            long_breakout_side = 'LOW'
            short_breakout_side = 'HIGH'
            long_entry = range_low
            short_entry = range_high
        else:
            long_break = close_price > range_high
            short_break = close_price < range_low
            long_breakout_side = 'HIGH'
            short_breakout_side = 'LOW'
            long_entry = range_high
            short_entry = range_low

        if ENTRY_MODE == 'equilibrium':
            equilibrium_entry = (float(range_high) + float(range_low)) / 2.0
            long_entry = equilibrium_entry
            short_entry = equilibrium_entry
        elif ENTRY_MODE == 'market':
            long_entry = close_price
            short_entry = close_price

        if long_break:
            breakout_range = high_price - low_price
            entry = long_entry
            stop, tp, stop_dist = build_stop_and_tp(
                direction='LONG',
                entry_price=entry,
                range_high=range_high,
                range_low=range_low,
                breakout_range=breakout_range,
            )
            base_risk_amount = FIXED_RISK_PER_TRADE if RISK_MODE == 'fixed' else (current_equity * (RISK_PERCENT_PER_TRADE / 100.0))
            risk_decision = resolve_prop_safe_risk(base_risk_amount, stop_dist, trades, day)
            if not risk_decision['allowed']:
                pending_cancel_counts['risk_guard'] += 1
                signal_used = True
                if 'DAILY_DD_LIMIT' in risk_decision['reason']:
                    skip_day = True
                continue
            risk_amount = risk_decision['risk_amount']
            position_size = risk_amount / stop_dist
            pending = {
                'direction': 'LONG',
                'entry_price': entry,
                'stop_loss': stop,
                'take_profit': tp,
                'range_high': range_high,
                'range_low': range_low,
                # entry_side tarihi uyumluluk icin tutuldu; artik kirilan siniri ifade ediyor.
                'entry_side': long_breakout_side,
                'breakout_side': long_breakout_side,
                'entry_mode': ENTRY_MODE,
                'breakout_time': index_values[i],
                'created_idx': i,
                'position_size': position_size,
                'risk_amount': risk_amount,
                'risk_step': risk_decision['step'],
                'risk_multiplier': risk_decision['multiplier'],
                'risk_guard_status': risk_decision['reason'],
                'initial_stop_dist': stop_dist,
                'be_triggered': False,
                'be_moved': False,
                'be_stop': None,
                'trail_activated': False,
                'trail_moved': False,
                'trail_stop': None,
                'trail_best_price': None,
                'trail_move_count': 0,
            }
            signal_used = True
            pending_created_now = True
        elif short_break:
            breakout_range = high_price - low_price
            entry = short_entry
            stop, tp, stop_dist = build_stop_and_tp(
                direction='SHORT',
                entry_price=entry,
                range_high=range_high,
                range_low=range_low,
                breakout_range=breakout_range,
            )
            base_risk_amount = FIXED_RISK_PER_TRADE if RISK_MODE == 'fixed' else (current_equity * (RISK_PERCENT_PER_TRADE / 100.0))
            risk_decision = resolve_prop_safe_risk(base_risk_amount, stop_dist, trades, day)
            if not risk_decision['allowed']:
                pending_cancel_counts['risk_guard'] += 1
                signal_used = True
                if 'DAILY_DD_LIMIT' in risk_decision['reason']:
                    skip_day = True
                continue
            risk_amount = risk_decision['risk_amount']
            position_size = risk_amount / stop_dist
            pending = {
                'direction': 'SHORT',
                'entry_price': entry,
                'stop_loss': stop,
                'take_profit': tp,
                'range_high': range_high,
                'range_low': range_low,
                # entry_side tarihi uyumluluk icin tutuldu; artik kirilan siniri ifade ediyor.
                'entry_side': short_breakout_side,
                'breakout_side': short_breakout_side,
                'entry_mode': ENTRY_MODE,
                'breakout_time': index_values[i],
                'created_idx': i,
                'position_size': position_size,
                'risk_amount': risk_amount,
                'risk_step': risk_decision['step'],
                'risk_multiplier': risk_decision['multiplier'],
                'risk_guard_status': risk_decision['reason'],
                'initial_stop_dist': stop_dist,
                'be_triggered': False,
                'be_moved': False,
                'be_stop': None,
                'trail_activated': False,
                'trail_moved': False,
                'trail_stop': None,
                'trail_best_price': None,
                'trail_move_count': 0,
            }
            signal_used = True
            pending_created_now = True

        # Optional same-bar pending fill for newly created signals (use with caution)
        if pending_created_now:
            can_fill = True if ENTRY_MODE == 'market' else ALLOW_SAME_BAR_FILL
            if not can_fill:
                if pending_touched(high_price, low_price, close_price, pending['direction'], pending['entry_price']):
                    pending_debug_counts['same_bar_touch_blocked'] += 1
            else:
                direction = pending['direction']
                touched = True if ENTRY_MODE == 'market' else pending_touched(high_price, low_price, close_price, direction, pending['entry_price'])
                
                if touched:
                    pending_debug_counts['touch_checks'] += 1
                ema_value = ema_values[i] if ema_values is not None else pd.NA
                ema_ok = ema_filter_passes(direction, pending['entry_price'], ema_value)
                
                if touched and ema_ok:
                    position = pending.copy()
                    position['entry_time'] = index_values[i]
                    position['entry_idx'] = i
                    pending = None
                    trades_today += 1
                    pending_debug_counts['fills'] += 1
                    
                    # Prevent time-travel logic: market orders entered at bar close cannot be stopped out in the same bar.
                    if ENTRY_MODE != 'market':
                        if BREAKEVEN_ENABLED:
                            triggered_now, moved_now = apply_breakeven(
                                position,
                                high_price,
                                low_price,
                                close_price,
                                i,
                                index_values[i],
                            )
                            if triggered_now:
                                breakeven_counts['triggered'] += 1
                            if moved_now:
                                breakeven_counts['moved'] += 1
                        
                        exit_reason, exit_price = resolve_exit_on_fill_bar(
                            position=position,
                            high=high_price,
                            low=low_price,
                        )
                        if exit_reason is not None:
                            trade = close_trade(
                                position=position,
                                exit_time=index_values[i],
                                exit_idx=i,
                                exit_price=exit_price,
                                exit_reason=exit_reason,
                            )
                            trades.append(trade)
                            current_equity += trade['pnl']
                            position = None
                        elif TRAILING_SL_ENABLED:
                            trail_activated_now, trail_moved_now = update_trailing_stop_after_bar(
                                position,
                                high_price,
                                low_price,
                                close_price,
                                i,
                                index_values[i],
                            )
                            if trail_activated_now:
                                trailing_counts['activated'] += 1
                            if trail_moved_now:
                                trailing_counts['moved'] += 1

                # Market orders that fail filters must not convert to pending limit orders.
                if ENTRY_MODE == 'market' and pending is not None:
                    pending = None

    if position is not None:
        final_idx = total_bars - 1
        trade = close_trade(
            position=position,
            exit_time=index_values[final_idx],
            exit_idx=final_idx,
            exit_price=close_values[final_idx],
            exit_reason='DATA_END',
        )
        trades.append(trade)
        current_equity += trade['pnl']
        position = None
    if pending is not None:
        pending_cancel_counts['data_end'] += 1
        pending = None

    if progress_enabled:
        print()

    if not trades:
        if not silent:
            print(f"Starting balance: {STARTING_BALANCE:.2f}")
            print(f"Risk mode: {RISK_MODE}")
            if RISK_MODE == 'fixed':
                print(f"Fixed risk per trade: {FIXED_RISK_PER_TRADE:.2f}")
            else:
                print(f"Risk per trade (%): {RISK_PERCENT_PER_TRADE:.2f}")
            print(f"Data range: {data_start} -> {data_end}")
            print(f"Data span: {data_days} days (~{data_years:.2f} years)")
            if SHOW_DEBUG_COUNTS:
                print(f"Pending cancelled: {pending_cancel_counts}")
                print(f"Pending debug: {pending_debug_counts}")
            print("No trades.")
        return None

    trades_df = pd.DataFrame(trades)
    trades_df['equity_after'] = STARTING_BALANCE + trades_df['pnl'].cumsum()
    trades_df['equity_before'] = trades_df['equity_after'] - trades_df['pnl']

    equity_curve = trades_df['equity_after']

    if run_distribution is None:
        run_distribution = DISTRIBUTION_ANALYSIS_ENABLED
    if run_curve_check is None:
        run_curve_check = CURVE_CHECK_ENABLED
    stats = compute_trade_stats(trades_df)
    max_drawdown = stats['max_drawdown']
    max_drawdown_pct = stats['max_drawdown_pct']
    window_robustness = None
    if run_window_robustness:
        window_robustness = evaluate_window_robustness(
            data=data,
            base_window=(RANGE_START_HOUR, RANGE_START_MINUTE, RANGE_END_HOUR, RANGE_END_MINUTE),
            base_stats=stats,
            shift_count=WINDOW_ROBUSTNESS_SHIFTS,
            use_data_driven_shift=WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT,
            shift_bars=WINDOW_ROBUSTNESS_SHIFT_BARS,
            max_shift_minutes=WINDOW_ROBUSTNESS_MAX_SHIFT_MINUTES,
            step_minutes=WINDOW_ROBUSTNESS_STEP_MINUTES,
            seed=WINDOW_ROBUSTNESS_SEED,
            min_trades=WINDOW_ROBUSTNESS_MIN_TRADES,
            pf_pass_threshold=WINDOW_ROBUSTNESS_PF_PASS,
        )
        if window_robustness is not None and CSV_EXPORT_ENABLED and WINDOW_ROBUSTNESS_RESULTS_CSV:
            try:
                window_robustness['rows'].to_csv(WINDOW_ROBUSTNESS_RESULTS_CSV, index=False)
            except Exception:
                pass

    if not silent:
        print("=" * 80)
        print("STATS")
        print("=" * 80)
        print(f"Total trades: {stats['total_trades']}")
        print(f"Win rate: {stats['win_rate']:.2f}%")
        print(f"Total PnL: {stats['total_pnl']:.4f}")
        print(f"Avg PnL: {stats['avg_pnl']:.4f}")
        print(f"Profit factor: {stats['profit_factor']:.2f}")
        print(f"Sharpe (per-trade): {stats['sharpe']:.2f}" if pd.notna(stats['sharpe']) else "Sharpe (per-trade): n/a")
        if pd.notna(stats['avg_win']):
            print(f"Avg win: {stats['avg_win']:.4f}")
        if pd.notna(stats['avg_loss']):
            print(f"Avg loss: {stats['avg_loss']:.4f}")
        if pd.notna(stats['payoff_ratio']):
            print(f"Payoff ratio: {stats['payoff_ratio']:.2f}")
        if pd.notna(stats['expectancy']):
            print(f"Expectancy (per trade): {stats['expectancy']:.4f}")
        if pd.notna(stats['avg_r']):
            print(f"Avg R: {stats['avg_r']:.2f}")
        print(f"Max consecutive wins: {stats['max_consecutive_wins']}")
        print(f"Max consecutive losses: {stats['max_consecutive_losses']}")
        print(f"Range window: {RANGE_START_HOUR:02d}:{RANGE_START_MINUTE:02d} to {RANGE_END_HOUR:02d}:{RANGE_END_MINUTE:02d}")
        try:
            print(f"Trade end: {local_trade_end_time.strftime('%H:%M')}")
        except Exception:
            print(f"Trade end: {TRADE_END_HOUR:02d}:{TRADE_END_MINUTE:02d}")
        print(f"Bar time mode: {BAR_TIME_MODE}")
        print(f"Signal start: {RANGE_END_TIME.strftime('%H:%M')}")
        print(f"Strategy mode: {STRATEGY_MODE}")
        print(f"Entry mode: {ENTRY_MODE}")
        print(f"SL placement: {SL_PLACEMENT}")
        if SL_PLACEMENT == 'range' and abs(float(SL_BOUNDARY_OFFSET)) > 0:
            print(f"SL boundary offset (legacy, unused): {SL_BOUNDARY_OFFSET}")
        print(f"EMA filter enabled: {EMA_FILTER_ENABLED}")
        print(f"EMA period: {EMA_PERIOD}")
        print(f"News filter enabled: {NEWS_FILTER_ENABLED}")
        if NEWS_FILTER_ENABLED:
            print(f"News calendar: {NEWS_CALENDAR_FILE}")
            print(f"News pre-event minutes: {NEWS_PRE_EVENT_MINUTES}")
            print(f"News impacts: {list(NEWS_IMPACTS)}")
            print(f"News currencies: {list(NEWS_BLOCK_CURRENCIES)}")
            print(f"News data UTC confirmed: {NEWS_DATA_UTC_CONFIRMED}")
            print(f"News max calendar->data gap days: {NEWS_MAX_CALENDAR_TO_DATA_GAP_DAYS}")
            print(f"News require events in data range: {NEWS_REQUIRE_EVENTS_IN_DATA_RANGE}")
        print(f"SL multiplier: {SL_MULTIPLIER}")
        print(f"RR ratio: {RR_RATIO}")
        print(f"Pending expiry bars: {local_pending_expiry}")
        print(f"Pending touch mode: {PENDING_TOUCH_MODE}")
        print(f"Price touch tolerance: {PRICE_TOUCH_TOLERANCE}")
        print(f"Cancel pending on opposite breakout: {CANCEL_PENDING_ON_OPPOSITE_BREAKOUT}")
        print(f"Intrabar exit policy: {INTRABAR_EXIT_POLICY}")
        print(f"Fill-bar exit policy: {FILL_BAR_EXIT_POLICY}")
        print(f"Backtest fixed cost/trade: {BACKTEST_COST_PER_TRADE:.2f}")
        print(f"Backtest cost points: {BACKTEST_COST_POINTS:.4f}")
        if EXECUTION_REALISM_REQUIRED and BACKTEST_COST_PER_TRADE == 0 and BACKTEST_COST_POINTS == 0:
            print("WARNING: Execution cost is zero; set commission + spread/slippage before treating results as live-ready.")
        print(f"Close position on day change: {CLOSE_POSITION_ON_DAY_CHANGE}")
        print(f"One signal per day: {ONE_SIGNAL_PER_DAY}")
        print(f"Breakeven enabled: {BREAKEVEN_ENABLED}")
        print(f"Window robustness enabled: {WINDOW_ROBUSTNESS_ENABLED}")
        if WINDOW_ROBUSTNESS_ENABLED:
            print(f"Window robustness shifts: {WINDOW_ROBUSTNESS_SHIFTS}")
            print(f"Window robustness data-driven shift: {WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT}")
            if WINDOW_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT:
                print(f"Window robustness shift bars: {WINDOW_ROBUSTNESS_SHIFT_BARS}")
            else:
                print(f"Window robustness max shift (min): {WINDOW_ROBUSTNESS_MAX_SHIFT_MINUTES}")
                print(f"Window robustness step (min): {WINDOW_ROBUSTNESS_STEP_MINUTES}")
            print(f"Window robustness min trades: {WINDOW_ROBUSTNESS_MIN_TRADES}")
            print(f"Window robustness PF pass: {WINDOW_ROBUSTNESS_PF_PASS}")
        if BREAKEVEN_ENABLED:
            print(f"BE trigger R: {BREAKEVEN_TRIGGER_R}")
            print(f"BE offset (R): {BREAKEVEN_OFFSET}")
            print(f"BE trigger mode: {BREAKEVEN_TRIGGER_MODE}")
            print(f"BE allow same bar: {BREAKEVEN_ALLOW_SAME_BAR}")
            print(f"BE triggered: {breakeven_counts['triggered']}")
            print(f"BE moved: {breakeven_counts['moved']}")
        print(f"Trailing SL enabled: {TRAILING_SL_ENABLED}")
        if TRAILING_SL_ENABLED:
            print(f"Trailing activate R: {TRAILING_SL_ACTIVATE_R}")
            print(f"Trailing distance R: {TRAILING_SL_DISTANCE_R}")
            print(f"Trailing step R: {TRAILING_SL_STEP_R}")
            print(f"Trailing trigger mode: {TRAILING_SL_TRIGGER_MODE}")
            print(f"Trailing lock breakeven: {TRAILING_SL_LOCK_BREAKEVEN}")
            print(f"Trailing cap at TP: {TRAILING_SL_CAP_AT_TP}")
            print(f"Trailing activated: {trailing_counts['activated']}")
            print(f"Trailing moved: {trailing_counts['moved']}")
        print(f"Starting balance: {STARTING_BALANCE:.2f}")
        print(f"Final balance: {equity_curve.iloc[-1]:.2f}")
        print(f"Risk mode: {RISK_MODE}")
        if RISK_MODE == 'fixed':
            print(f"Fixed risk per trade: {FIXED_RISK_PER_TRADE:.2f}")
        else:
            print(f"Risk per trade (%): {RISK_PERCENT_PER_TRADE:.2f}")
        print(
            "Dynamic risk guard: "
            f"enabled={RISK_SETTINGS.get('enabled')}, mode={RISK_SETTINGS.get('mode')}, "
            f"max_steps={RISK_SETTINGS.get('max_steps')}, "
            f"max_risk={RISK_SETTINGS.get('max_risk_per_trade_pct')}% equity"
        )
        print(
            "Prop risk enforcement: "
            f"{RISK_SETTINGS.get('enforce_prop_limits')} "
            f"(DD safety buffer={RISK_SETTINGS.get('prop_safety_buffer_pct')}%)"
        )
        if RISK_SETTINGS.get('enabled') and RISK_SETTINGS.get('mode') == 'anti_martingale':
            print(
                "Anti-martingale ladder: "
                f"+{RISK_SETTINGS.get('anti_martingale_win_increment')} on win, "
                f"loss x{RISK_SETTINGS.get('anti_martingale_loss_multiplier')}, "
                f"floor={RISK_SETTINGS.get('min_risk_amount')}, "
                f"cap={RISK_SETTINGS.get('max_risk_amount')}"
            )
        print(f"Risk-guarded signals skipped: {pending_cancel_counts['risk_guard']}")
        print(f"Data range: {data_start} -> {data_end}")
        print(f"Data span: {data_days} days (~{data_years:.2f} years)")
        print(f"Max drawdown: {max_drawdown:.4f}")
        print(f"Max drawdown %: {max_drawdown_pct:.2f}%")
        if SHOW_DEBUG_COUNTS:
            print(f"Pending cancelled: {pending_cancel_counts}")
            print(f"Pending debug: {pending_debug_counts}")

        if SHOW_WEEKDAY_STATS:
            wd = compute_weekday_stats(trades_df)
            if wd is not None:
                print("-" * 80)
                print("WEEKDAY PERFORMANCE")
                print(wd.to_string(index=False))
        monthly = compute_monthly_returns(trades_df, starting_balance=STARTING_BALANCE)
        if monthly is not None and not monthly.empty:
            print("-" * 80)
            print("MONTHLY RETURNS")
            monthly_display = monthly.copy()
            monthly_display['month'] = monthly_display['month'].dt.strftime('%Y-%m')
            monthly_display['return_pct'] = monthly_display['return_pct'].map(
                lambda x: f"{x:.2f}%" if pd.notna(x) else "n/a"
            )
            monthly_display['max_pct'] = monthly_display['max_pct'].map(
                lambda x: f"{x:.2f}%" if pd.notna(x) else "n/a"
            )
            print(monthly_display[['month', 'return_pct', 'max_pct']].to_string(index=False))
            avg_monthly = monthly['return_pct'].mean()
            if pd.notna(avg_monthly):
                print(f"Avg monthly return: {avg_monthly:.2f}%")
        if window_robustness is not None:
            print("-" * 80)
            print("WINDOW ROBUSTNESS (RANDOM SHIFTS)")
            print(
                f"Base window: "
                f"{window_robustness['base_window'][0]:02d}:{window_robustness['base_window'][1]:02d} -> "
                f"{window_robustness['base_window'][2]:02d}:{window_robustness['base_window'][3]:02d}"
            )
            print(f"Shift attempts: {window_robustness['attempted_shifts']}")
            print(f"Shiftable windows: {window_robustness['shiftable_shifts']}")
            print(f"Executed shifts: {window_robustness['executed_shifts']}")
            print(f"Errors: {window_robustness['error_shifts']}")
            print(f"Valid shifts: {window_robustness['valid_shifts']}")
            print(
                "Resolved shift setup: "
                f"data_tf={window_robustness['data_timeframe_minutes']}m, "
                f"step={window_robustness['resolved_shift_step_minutes']}m, "
                f"max={window_robustness['resolved_max_shift_minutes']}m"
            )
            print(f"PF pass ratio: {window_robustness['pf_pass_ratio'] * 100.0:.1f}%")
            print(f"PnL > 0 ratio: {window_robustness['pnl_pass_ratio'] * 100.0:.1f}%")
            if pd.notna(window_robustness['median_shift_pf']):
                print(f"Median shifted PF: {window_robustness['median_shift_pf']:.2f}")
            if pd.notna(window_robustness['pf_std']):
                print(f"Shift PF std: {window_robustness['pf_std']:.2f}")
            if pd.notna(window_robustness['base_pf_percentile']):
                print(f"Base PF percentile vs shifts: {window_robustness['base_pf_percentile']:.1f}%")
            print(f"Robustness score (0-100): {window_robustness['robustness_score']:.1f}")
            if CSV_EXPORT_ENABLED and WINDOW_ROBUSTNESS_RESULTS_CSV:
                print(f"Shift details saved: {WINDOW_ROBUSTNESS_RESULTS_CSV}")
            detail_rows = window_robustness['rows'].copy()
            if WINDOW_ROBUSTNESS_PRINT_ROWS > 0 and len(detail_rows) > WINDOW_ROBUSTNESS_PRINT_ROWS:
                detail_rows = detail_rows.head(WINDOW_ROBUSTNESS_PRINT_ROWS)
            cols = ['shift_min', 'range_start', 'range_end', 'status', 'total_trades', 'profit_factor', 'total_pnl']
            cols = [col for col in cols if col in detail_rows.columns]
            if cols:
                print(detail_rows[cols].to_string(index=False))

    if run_distribution and not silent:
        dist = compute_distribution(data, trades_df, DISTRIBUTION_MODE, DISTRIBUTION_HORIZON_BARS)
        if dist:
            print("-" * 80)
            print("DISTRIBUTION")
            print(f"Mode: {DISTRIBUTION_MODE}")
            print(f"Trades analyzed: {dist['count']}")
            print(f"Avg MFE (R): {dist['avg_mfe_r']:.2f}")
            print(f"Avg MAE (R): {dist['avg_mae_r']:.2f}")
            print(f"Hit >=1R: {dist['hit_1r_pct']:.2f}%")
            print(f"Hit >=2R: {dist['hit_2r_pct']:.2f}%")
            print(f"Hit >=3R: {dist['hit_3r_pct']:.2f}%")

    if PROP_ENABLED and not silent:
        target = _resolve_prop_value(PROP_PROFIT_TARGET_PCT, PROP_PROFIT_TARGET_ABS, STARTING_BALANCE)
        max_dd = _resolve_prop_value(PROP_MAX_DD_PCT, PROP_MAX_DD_ABS, STARTING_BALANCE)
        daily_dd = _resolve_prop_value(PROP_DAILY_DD_PCT, PROP_DAILY_DD_ABS, STARTING_BALANCE)
        prop_result = evaluate_prop_path(
            trades_df,
            STARTING_BALANCE,
            target,
            max_dd,
            PROP_MAX_DD_MODE,
            daily_dd,
            PROP_DAILY_DD_MODE,
        )
        print("-" * 80)
        print("PROP FIRM CHECK")
        if prop_result is None:
            print("Result: INSUFFICIENT_DATA")
        else:
            print(f"Profit target: {target:.2f}")
            print(f"Max drawdown: {max_dd:.2f} ({PROP_MAX_DD_MODE})")
            if daily_dd is not None:
                print(f"Daily drawdown: {daily_dd:.2f} ({PROP_DAILY_DD_MODE})")
            else:
                print("Daily drawdown: disabled (set PROP_DAILY_DD_PCT or PROP_DAILY_DD_ABS)")
            print(f"Outcome (actual order): {prop_result['result']}")

        if PROP_MC_ENABLED:
            mc = simulate_prop_mc(
                trades_df,
                STARTING_BALANCE,
                target,
                max_dd,
                PROP_MAX_DD_MODE,
                daily_dd,
                PROP_DAILY_DD_MODE,
                PROP_MC_ITER,
                PROP_MC_SEED,
                PROP_MC_SHUFFLE_BY,
            )
            if mc is None:
                print("Pass/Fail Monte Carlo: n/a")
            else:
                print(f"Pass chance: {mc['pass_rate']:.1f}%")
                print(f"Fail (max DD) chance: {mc['fail_max_dd_rate']:.1f}%")
                if daily_dd is not None:
                    print(f"Fail (daily DD) chance: {mc['fail_daily_rate']:.1f}%")
                print(f"No target reached: {mc['no_target_rate']:.1f}%")

        if prop_result is not None and prop_result['result'] == 'PASS_TARGET':
            post_trades = get_post_pass_trades(trades_df, prop_result.get('pass_index'))
            print("-" * 80)
            print("POST-PASS REPORT")
            if post_trades is None or post_trades.empty:
                print("No trades after pass.")
            else:
                pass_equity = prop_result.get('equity', STARTING_BALANCE)
                pass_time = prop_result.get('pass_time')
                if pass_time is not None:
                    print(f"Pass time: {pass_time}")
                print(f"Equity at pass: {pass_equity:.2f}")
                post_stats = compute_trade_stats(post_trades, starting_balance=pass_equity)
                print(f"Trades after pass: {post_stats['total_trades']}")
                print(f"Win rate: {post_stats['win_rate']:.2f}%")
                print(f"Profit factor: {post_stats['profit_factor']:.2f}")
                print(f"Sharpe: {post_stats['sharpe']:.2f}")
                print(f"Total PnL: {post_stats['total_pnl']:.2f}")
                print(f"Max drawdown %: {post_stats['max_drawdown_pct']:.2f}%")

    if OOS_REPORT_ENABLED and not silent:
        print("-" * 80)
        print("OOS REPORT")
        if OOS_START_DATE is None and OOS_END_DATE is None and OOS_PCT is None:
            print("OOS window not set. Set OOS_START_DATE/OOS_END_DATE or OOS_PCT.")
        else:
            oos_trades = trades_df
            start_ts = pd.to_datetime(OOS_START_DATE) if OOS_START_DATE else None
            end_ts = pd.to_datetime(OOS_END_DATE) if OOS_END_DATE else None
            if start_ts is not None or end_ts is not None:
                if start_ts is not None:
                    oos_trades = oos_trades[oos_trades['entry_time'] >= start_ts]
                if end_ts is not None:
                    oos_trades = oos_trades[oos_trades['entry_time'] < (end_ts + pd.Timedelta(days=1))]
                start_label = OOS_START_DATE if OOS_START_DATE else "start"
                end_label = OOS_END_DATE if OOS_END_DATE else "end"
                print(f"Range: {start_label} -> {end_label} (entry_time, inclusive)")
            else:
                split_ts, _, oos_trades = split_trades_by_pct(oos_trades, 1.0 - OOS_PCT)
                if split_ts is None:
                    print("OOS window invalid for OOS_PCT (not enough trades).")
                    oos_trades = oos_trades.iloc[0:0]
                else:
                    print(f"Range: last {OOS_PCT * 100:.1f}% of trades (from {split_ts.date()})")

            if oos_trades.empty:
                print("No trades in OOS window.")
            else:
                oos_stats = compute_trade_stats(oos_trades)
                print(f"Trades: {oos_stats['total_trades']}")
                print(f"Win rate: {oos_stats['win_rate']:.2f}%")
                print(f"Profit factor: {oos_stats['profit_factor']:.2f}")
                print(f"Sharpe: {oos_stats['sharpe']:.2f}")
                print(f"Total PnL: {oos_stats['total_pnl']:.2f}")
                print(f"Max drawdown %: {oos_stats['max_drawdown_pct']:.2f}%")
                if PROP_ENABLED:
                    target = _resolve_prop_value(PROP_PROFIT_TARGET_PCT, PROP_PROFIT_TARGET_ABS, STARTING_BALANCE)
                    max_dd = _resolve_prop_value(PROP_MAX_DD_PCT, PROP_MAX_DD_ABS, STARTING_BALANCE)
                    daily_dd = _resolve_prop_value(PROP_DAILY_DD_PCT, PROP_DAILY_DD_ABS, STARTING_BALANCE)
                    prop_oos = evaluate_prop_path(
                        oos_trades,
                        STARTING_BALANCE,
                        target,
                        max_dd,
                        PROP_MAX_DD_MODE,
                        daily_dd,
                        PROP_DAILY_DD_MODE,
                    )
                    if prop_oos is None:
                        print("Prop (OOS): INSUFFICIENT_DATA")
                    else:
                        print(f"Prop (OOS): {prop_oos['result']}")
                    if PROP_MC_ENABLED:
                        mc_oos = simulate_prop_mc(
                            oos_trades,
                            STARTING_BALANCE,
                            target,
                            max_dd,
                            PROP_MAX_DD_MODE,
                            daily_dd,
                            PROP_DAILY_DD_MODE,
                            PROP_MC_ITER,
                            PROP_MC_SEED,
                            PROP_MC_SHUFFLE_BY,
                        )
                        if mc_oos is None:
                            print("Prop MC (OOS): n/a")
                        else:
                            print(f"Prop MC pass chance (OOS): {mc_oos['pass_rate']:.1f}%")

    if EDGE_GUARD_ENABLED and not silent:
        edge_guard = build_edge_guard_report(trades_df)
        print("-" * 80)
        print("EDGE GUARD (CHRONOLOGICAL IS/OOS)")
        if edge_guard['verdict'] == 'INSUFFICIENT_DATA':
            print("Verdict: INSUFFICIENT_DATA")
        else:
            is_stats = edge_guard['is_stats']
            oos_stats = edge_guard['oos_stats']
            print(f"Split: {edge_guard['split_ts'].date()} | IS -> OOS")
            print(
                f"IS: trades={is_stats['total_trades']}, PF={is_stats['profit_factor']:.2f}, "
                f"PnL={is_stats['total_pnl']:.2f}, DD={is_stats['max_drawdown_pct']:.2f}%"
            )
            print(
                f"OOS: trades={oos_stats['total_trades']}, PF={oos_stats['profit_factor']:.2f}, "
                f"PnL={oos_stats['total_pnl']:.2f}, DD={oos_stats['max_drawdown_pct']:.2f}%"
            )
            print(f"Verdict: {edge_guard['verdict']} | Flags: {', '.join(edge_guard['flags']) if edge_guard['flags'] else 'None'}")

    if run_curve_check and not silent:
        curve_trades = trades_df
        curve_start_label = "all"
        curve_note = None
        if CURVE_START_YEAR is not None:
            curve_trades = trades_df[trades_df['entry_time'].dt.year >= CURVE_START_YEAR]
            curve_start_label = str(CURVE_START_YEAR)
            if len(curve_trades) == len(trades_df):
                curve_note = "Note: CURVE_START_YEAR filter did not change trades (all trades >= year)."
        split_label = None
        if CURVE_SPLIT_DATE:
            split_ts = pd.to_datetime(CURVE_SPLIT_DATE)
            split_label = CURVE_SPLIT_DATE
        else:
            sorted_times = curve_trades['entry_time'].sort_values()
            split_idx = int(len(sorted_times) * 0.8)
            if split_idx <= 0 or split_idx >= len(sorted_times):
                split_ts = None
            else:
                split_ts = pd.to_datetime(sorted_times.iloc[split_idx])
                split_label = f"auto_80_20 @ {split_ts.date()}"

        print("-" * 80)
        print("CURVE-FIT CHECK")
        if split_ts is None:
            print("Result: INSUFFICIENT_DATA")
        else:
            is_trades = curve_trades[curve_trades['entry_time'] < split_ts]
            oos_trades = curve_trades[curve_trades['entry_time'] >= split_ts]
            is_stats = compute_trade_stats(is_trades)
            oos_stats = compute_trade_stats(oos_trades)

            print(f"Start year: {curve_start_label}")
            print(f"Curve trades: {len(curve_trades)}/{len(trades_df)}")
            if curve_note:
                print(curve_note)
            print(f"Split: {split_label}")
            print(f"IS trades: {is_stats['total_trades']}, OOS trades: {oos_stats['total_trades']}")

            if is_stats['total_trades'] < CURVE_MIN_TRADES or oos_stats['total_trades'] < CURVE_MIN_TRADES:
                print("Result: INSUFFICIENT_DATA")
            else:
                flags = []
                if oos_stats['profit_factor'] < CURVE_PF_MIN:
                    flags.append('PF')
                if pd.notna(oos_stats['sharpe']) and oos_stats['sharpe'] < CURVE_SHARPE_MIN:
                    flags.append('Sharpe')
                if oos_stats['win_rate'] < (is_stats['win_rate'] - CURVE_WINRATE_DROP):
                    flags.append('WinRateDrop')
                if oos_stats['total_pnl'] < 0:
                    flags.append('OOS_PnL_Negative')

                if len(flags) >= 3:
                    risk = 'HIGH'
                elif len(flags) >= 1:
                    risk = 'MEDIUM'
                else:
                    risk = 'LOW'

                print(f"IS PF: {is_stats['profit_factor']:.2f} | OOS PF: {oos_stats['profit_factor']:.2f}")
                print(f"IS Win%: {is_stats['win_rate']:.2f} | OOS Win%: {oos_stats['win_rate']:.2f}")
                print(f"IS Sharpe: {is_stats['sharpe']:.2f} | OOS Sharpe: {oos_stats['sharpe']:.2f}" if pd.notna(oos_stats['sharpe']) else "Sharpe: n/a")
                print(f"Flags: {', '.join(flags) if flags else 'None'}")
                print(f"Result: CURVE_FIT_RISK_{risk}")

    if WF_ENABLED and not silent:
        wf_stats = walk_forward_oos_pf_pct(
            trades_df,
            WF_TRAIN_PCT,
            WF_TEST_PCT,
            WF_STEP_PCT,
            WF_PF_MIN,
        )
        print("-" * 80)
        print("ROLLING OOS WINDOW CHECK")
        print("Note: this is a rolling OOS slice report, not parameter re-optimization.")
        if wf_stats is None or wf_stats['folds'] < WF_MIN_FOLDS:
            print("Result: INSUFFICIENT_DATA")
        else:
            print(
                f"Train/Test/Step (days): "
                f"{wf_stats['train_days']}/{wf_stats['test_days']}/{wf_stats['step_days']}"
            )
            print(f"Folds: {wf_stats['folds']}")
            print(f"Median OOS PF: {wf_stats['median_pf']:.2f}")
            print(f"Min OOS PF: {wf_stats['min_pf']:.2f}")
            print(f"% folds below PF {WF_PF_MIN:.2f}: {wf_stats['pct_below_min']:.1f}%")

    if not silent:
        print("=" * 80)

    if save_trades and CSV_EXPORT_ENABLED:
        trades_df.to_csv('trades.csv', index=False)
        if not silent:
            print(f"Trades saved: {len(trades_df)} -> trades.csv")

    if show_plots:
        if SHOW_BALANCE_PLOT:
            plot_balance_curve(trades_df)
        if SHOW_TRADE_PLOTS:
            plot_random_trades(data, trades_df, n=TRADE_PLOTS_COUNT, window=TRADE_PLOTS_WINDOW)

    return trades_df


def set_range_window(start_h, start_m, end_h, end_m):
    global RANGE_START_HOUR, RANGE_START_MINUTE, RANGE_END_HOUR, RANGE_END_MINUTE
    global RANGE_START_TIME, RANGE_END_TIME
    RANGE_START_HOUR = int(start_h)
    RANGE_START_MINUTE = int(start_m)
    RANGE_END_HOUR = int(end_h)
    RANGE_END_MINUTE = int(end_m)
    RANGE_START_TIME = pd.Timestamp(f'{RANGE_START_HOUR:02d}:{RANGE_START_MINUTE:02d}:00').time()
    RANGE_END_TIME = pd.Timestamp(f'{RANGE_END_HOUR:02d}:{RANGE_END_MINUTE:02d}:00').time()


def _safe_score_component(value, cap):
    if pd.isna(value):
        return 0.0
    val = float(value)
    if val == float('inf'):
        return float(GRID_INF_SCORE_FACTOR)
    if val < 0:
        return 0.0
    return min(val, float(cap)) / float(cap)


def compute_edge_score_from_row(row):
    # Edge score is the primary grid rank. It caps infinite PF values and
    # penalizes thin samples so the grid does not over-rank lucky OOS slices.
    pf_score = _safe_score_component(row.get('profit_factor', float('nan')), GRID_INF_METRIC_CAP)
    oos_pf_score = _safe_score_component(row.get('oos_profit_factor', float('nan')), GRID_INF_METRIC_CAP)
    wf_score = _safe_score_component(row.get('wf_median_pf', float('nan')), max(3.0, GRID_INF_METRIC_CAP))
    sharpe_score = _safe_score_component(row.get('sharpe', float('nan')), 3.0)
    pnl_score = _positive_score_component(row.get('total_pnl', float('nan')), STARTING_BALANCE * 4.0)

    dd = row.get('max_drawdown_pct', float('nan'))
    if pd.isna(dd):
        dd_score = 0.0
    else:
        dd_score = max(0.0, 1.0 - (float(dd) / 25.0))

    mc_dd = row.get('mc_dd_p95', float('nan'))
    if pd.isna(mc_dd):
        mc_dd_score = 1.0 if not GRID_MC_ENABLED else 0.5
    else:
        mc_dd_score = max(0.0, 1.0 - (float(mc_dd) / 25.0))

    total_trades = _trade_count_from_row(row, 'total_trades')
    oos_trades = _trade_count_from_row(row, 'oos_trades')
    wf_folds = _trade_count_from_row(row, 'wf_folds')
    trade_sample_score = min(total_trades / float(GRID_MIN_TRADES_FOR_RANK), 1.0)
    oos_sample_score = (
        min(oos_trades / float(GRID_MIN_OOS_TRADES_FOR_RANK), 1.0)
        if GRID_SPLIT_ENABLED
        else 1.0
    )
    wf_sample_score = min(wf_folds / float(max(WF_MIN_FOLDS, 1)), 1.0) if WF_ENABLED else 1.0
    sample_score = (0.55 * trade_sample_score) + (0.30 * oos_sample_score) + (0.15 * wf_sample_score)

    robustness = row.get('robustness_score', float('nan'))
    if pd.isna(robustness):
        score = 100.0 * (
            (0.18 * pf_score)
            + (0.22 * oos_pf_score)
            + (0.16 * wf_score)
            + (0.08 * sharpe_score)
            + (0.11 * dd_score)
            + (0.08 * mc_dd_score)
            + (0.07 * pnl_score)
            + (0.10 * sample_score)
        )
    else:
        robustness_score = max(0.0, min(float(robustness), 100.0)) / 100.0
        score = 100.0 * (
            (0.14 * pf_score)
            + (0.18 * oos_pf_score)
            + (0.12 * wf_score)
            + (0.06 * sharpe_score)
            + (0.09 * dd_score)
            + (0.06 * mc_dd_score)
            + (0.06 * pnl_score)
            + (0.09 * sample_score)
            + (0.20 * robustness_score)
        )

    score *= 0.50 + (0.50 * sample_score)

    if not pd.isna(row.get('total_pnl', float('nan'))) and float(row.get('total_pnl')) <= 0:
        score *= 0.25
    if GRID_SPLIT_ENABLED and not pd.isna(row.get('oos_total_pnl', float('nan'))) and float(row.get('oos_total_pnl')) <= 0:
        score *= 0.65
    if WF_ENABLED and not pd.isna(row.get('wf_pct_below_min', float('nan'))):
        below_ratio = max(0.0, min(float(row.get('wf_pct_below_min')) / 100.0, 1.0))
        score *= 1.0 - (0.35 * below_ratio)

    return float(max(0.0, min(score, 100.0)))


def _fmt_compact_num(value, decimals=2):
    if pd.isna(value):
        return '-'
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if val == float('inf'):
        return 'inf'
    return f"{val:.{int(decimals)}f}"


def _fmt_compact_int(value):
    if pd.isna(value):
        return '-'
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _fmt_compact_bool(value):
    return 'T' if bool(value) else 'F'


def _dedupe_preserve_order(values):
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def sort_grid_results_for_metric(results_df, metric):
    if results_df is None or results_df.empty or metric not in results_df.columns:
        return results_df
    metric_caps = {
        'profit_factor': GRID_INF_METRIC_CAP,
        'oos_profit_factor': GRID_INF_METRIC_CAP,
        'wf_median_pf': GRID_INF_METRIC_CAP,
        'wf_min_pf': GRID_INF_METRIC_CAP,
        'sharpe': 5.0,
        'robustness_score': 100.0,
        'edge_score': 100.0,
    }
    rank_df = results_df.copy()
    rank_df['_metric_rank'] = rank_df[metric].apply(
        lambda value: _finite_rank_value(value, metric_caps.get(metric))
    )
    rank_df['_edge_rank'] = rank_df.get('edge_score', pd.Series([0.0] * len(rank_df), index=rank_df.index)).apply(
        lambda value: _finite_rank_value(value, 100.0)
    )
    sort_cols = ['_metric_rank', '_edge_rank']
    ascending = [False, False]
    for col in ['total_pnl', 'total_trades', 'oos_trades']:
        if col in rank_df.columns:
            sort_cols.append(col)
            ascending.append(False)
    rank_df = rank_df.sort_values(by=sort_cols, ascending=ascending, kind='mergesort')
    return rank_df.drop(columns=['_metric_rank', '_edge_rank'])


def print_grid_top_compact(top_df, metric):
    if top_df is None or top_df.empty:
        return
    print("-" * 80)
    rank_note = " rank-capped" if metric in {'profit_factor', 'oos_profit_factor', 'wf_median_pf', 'wf_min_pf', 'sharpe'} else ""
    print(f"TOP {len(top_df)} by {metric}{rank_note} (compact)")
    for rank, (_, row) in enumerate(top_df.iterrows(), start=1):
        metric_value = _fmt_compact_num(row.get(metric, float('nan')), decimals=3)
        line = (
            f"{rank:>2}) W={row.get('RANGE_START', '?')}-{row.get('RANGE_END', '?')} "
            f"SM={row.get('STRATEGY_MODE', '?')} "
            f"EM={row.get('ENTRY_MODE', '?')} "
            f"SLP={row.get('SL_PLACEMENT', '?')} "
            f"SL={_fmt_compact_num(row.get('SL_MULTIPLIER', float('nan')), 1)} "
            f"RR={_fmt_compact_num(row.get('RR_RATIO', float('nan')), 1)} "
            f"BE={_fmt_compact_bool(row.get('BREAKEVEN_ENABLED', False))}"
        )
        if bool(row.get('BREAKEVEN_ENABLED', False)):
            line += (
                f"({ _fmt_compact_num(row.get('BREAKEVEN_TRIGGER_R', float('nan')), 2) }/"
                f"{ _fmt_compact_num(row.get('BREAKEVEN_OFFSET', float('nan')), 2) })"
            )
        trail_enabled = bool(row.get('TRAILING_SL_ENABLED', False))
        if trail_enabled:
            line += (
                f" TR={_fmt_compact_num(row.get('TRAILING_SL_ACTIVATE_R', float('nan')), 2)}/"
                f"{_fmt_compact_num(row.get('TRAILING_SL_DISTANCE_R', float('nan')), 2)}/"
                f"{_fmt_compact_num(row.get('TRAILING_SL_STEP_R', float('nan')), 2)}"
            )
        else:
            line += " TR=-"
        ema_enabled = bool(row.get('EMA_FILTER_ENABLED', False))
        if ema_enabled:
            line += f" EMA={_fmt_compact_int(row.get('EMA_PERIOD', float('nan')))}"
        else:
            line += " EMA=-"
        if 'PENDING_EXPIRY_BARS' in row.index:
            line += f" EXP={_fmt_compact_int(row.get('PENDING_EXPIRY_BARS', float('nan')))}"
        if 'TRADE_END' in row.index and pd.notna(row.get('TRADE_END')):
            line += f" END={row.get('TRADE_END')}"
        line += f" | {metric}={metric_value}"
        if metric != 'edge_score':
            line += f" edge={_fmt_compact_num(row.get('edge_score', float('nan')), 2)}"
        if metric != 'robustness_score':
            line += f" rob={_fmt_compact_num(row.get('robustness_score', float('nan')), 1)}"
        line += (
            f" tr={_fmt_compact_int(row.get('total_trades', float('nan')))} "
            f"oosTr={_fmt_compact_int(row.get('oos_trades', float('nan')))} "
            f"oosPF={_fmt_compact_num(row.get('oos_profit_factor', float('nan')), 3)} "
            f"wfPF={_fmt_compact_num(row.get('wf_median_pf', float('nan')), 3)} "
            f"dd={_fmt_compact_num(row.get('max_drawdown_pct', float('nan')), 2)} "
            f"mc95={_fmt_compact_num(row.get('mc_dd_p95', float('nan')), 2)} "
            f"mc99={_fmt_compact_num(row.get('mc_dd_p99', float('nan')), 2)}"
        )
        if metric != 'total_pnl':
            line += f" pnl={_fmt_compact_num(row.get('total_pnl', float('nan')), 2)}"
        print(line)


def print_grid_top_table(top_df, metric):
    if top_df is None or top_df.empty:
        return
    print("-" * 80)
    rank_note = " rank-capped" if metric in {'profit_factor', 'oos_profit_factor', 'wf_median_pf', 'wf_min_pf', 'sharpe'} else ""
    print(f"TOP {len(top_df)} by {metric}{rank_note}")
    cols = _dedupe_preserve_order(
        [
            'SL_MULTIPLIER',
            'RR_RATIO',
            'RANGE_START',
            'RANGE_END',
            'STRATEGY_MODE',
            'ENTRY_MODE',
            'SL_PLACEMENT',
            'BREAKEVEN_ENABLED',
            'BREAKEVEN_TRIGGER_R',
            'BREAKEVEN_OFFSET',
            'TRAILING_SL_ENABLED',
            'TRAILING_SL_ACTIVATE_R',
            'TRAILING_SL_DISTANCE_R',
            'TRAILING_SL_STEP_R',
            'TRAILING_SL_LOCK_BREAKEVEN',
            'EMA_FILTER_ENABLED',
            'EMA_PERIOD',
            'PENDING_EXPIRY_BARS',
            'TRADE_END',
            metric,
            'edge_score',
            'robustness_score',
            'total_trades',
            'oos_profit_factor',
            'oos_trades',
            'wf_median_pf',
            'wf_folds',
            'rand_pf_min',
            'mc_dd_p95',
            'mc_dd_p99',
            'total_pnl',
        ]
    )
    cols = [col for col in cols if col in top_df.columns]
    display_df = top_df[cols].copy()
    for col in display_df.columns:
        if pd.api.types.is_float_dtype(display_df[col]):
            display_df[col] = display_df[col].round(4)
    with pd.option_context(
        'display.max_columns',
        None,
        'display.width',
        240,
        'display.expand_frame_repr',
        False,
        'display.max_colwidth',
        24,
    ):
        print(display_df.to_string(index=False))


def _build_grid_param_combos():
    combos = []
    strategy_mode_options = resolve_grid_strategy_mode_options()
    entry_mode_options = resolve_grid_entry_mode_options()
    sl_placement_options = resolve_grid_sl_placement_options()

    def _ensure_list(val, name=None):
        values = _as_option_list(val)
        if not values:
            raise ValueError(f"{name or 'grid option'} cannot be empty")
        return values

    pending_options = _ensure_list(GRID_PENDING_EXPIRY_BARS_OPTIONS, 'GRID_PENDING_EXPIRY_BARS_OPTIONS')
    if (
        isinstance(GRID_TRADE_END_OPTIONS, tuple)
        and len(GRID_TRADE_END_OPTIONS) == 2
        and not isinstance(GRID_TRADE_END_OPTIONS[0], (list, tuple, set))
    ):
        trade_end_options = [GRID_TRADE_END_OPTIONS]
    else:
        trade_end_options = _ensure_list(GRID_TRADE_END_OPTIONS, 'GRID_TRADE_END_OPTIONS')

    be_combos = []
    for be_enabled_raw in GRID_BREAKEVEN_ENABLED_OPTIONS:
        be_enabled = bool(be_enabled_raw)
        be_triggers = GRID_BREAKEVEN_TRIGGER_R_OPTIONS if be_enabled else [BREAKEVEN_TRIGGER_R]
        be_offsets = GRID_BREAKEVEN_OFFSET_OPTIONS if be_enabled else [BREAKEVEN_OFFSET]
        be_modes = GRID_BREAKEVEN_TRIGGER_MODE_OPTIONS if be_enabled else [BREAKEVEN_TRIGGER_MODE]
        be_same_bar_options = GRID_BREAKEVEN_ALLOW_SAME_BAR_OPTIONS if be_enabled else [BREAKEVEN_ALLOW_SAME_BAR]
        for be_trigger_r, be_offset, be_mode, be_same_bar in itertools.product(
            be_triggers,
            be_offsets,
            be_modes,
            be_same_bar_options,
        ):
            if not is_valid_breakeven_settings(be_enabled, be_trigger_r, be_offset):
                continue
            be_combos.append(
                {
                    'be_enabled': bool(be_enabled),
                    'be_trigger_r': float(be_trigger_r),
                    'be_offset': float(be_offset),
                    'be_trigger_mode': str(be_mode),
                    'be_allow_same_bar': bool(be_same_bar),
                }
            )

    trail_combos = []
    for trail_enabled_raw in GRID_TRAILING_SL_ENABLED_OPTIONS:
        trail_enabled = bool(trail_enabled_raw)
        trail_activate_options = GRID_TRAILING_SL_ACTIVATE_R_OPTIONS if trail_enabled else [TRAILING_SL_ACTIVATE_R]
        trail_distance_options = GRID_TRAILING_SL_DISTANCE_R_OPTIONS if trail_enabled else [TRAILING_SL_DISTANCE_R]
        trail_step_options = GRID_TRAILING_SL_STEP_R_OPTIONS if trail_enabled else [TRAILING_SL_STEP_R]
        trail_lock_options = GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS if trail_enabled else [TRAILING_SL_LOCK_BREAKEVEN]
        trail_mode_options = GRID_TRAILING_SL_TRIGGER_MODE_OPTIONS if trail_enabled else [TRAILING_SL_TRIGGER_MODE]
        trail_cap_options = GRID_TRAILING_SL_CAP_AT_TP_OPTIONS if trail_enabled else [TRAILING_SL_CAP_AT_TP]
        for trail_activate_r, trail_distance_r, trail_step_r, trail_lock_be, trail_mode, trail_cap_tp in itertools.product(
            trail_activate_options,
            trail_distance_options,
            trail_step_options,
            trail_lock_options,
            trail_mode_options,
            trail_cap_options,
        ):
            trail_combos.append(
                {
                    'trail_enabled': bool(trail_enabled),
                    'trail_activate_r': float(trail_activate_r),
                    'trail_distance_r': float(trail_distance_r),
                    'trail_step_r': float(trail_step_r),
                    'trail_lock_be': bool(trail_lock_be),
                    'trail_trigger_mode': str(trail_mode),
                    'trail_cap_tp': bool(trail_cap_tp),
                }
            )

    ema_combos = []
    for ema_enabled_raw in GRID_EMA_FILTER_OPTIONS:
        ema_enabled = bool(ema_enabled_raw)
        ema_periods = GRID_EMA_PERIOD_OPTIONS if ema_enabled else [EMA_PERIOD]
        for ema_period in ema_periods:
            ema_combos.append({'ema_enabled': bool(ema_enabled), 'ema_period': int(ema_period)})

    risk_combos = []
    for risk_mode in GRID_RISK_MODE_OPTIONS:
        percent_options = GRID_RISK_PERCENT_PER_TRADE_OPTIONS if str(risk_mode) == 'percent' else [RISK_PERCENT_PER_TRADE]
        fixed_options = GRID_FIXED_RISK_PER_TRADE_OPTIONS if str(risk_mode) == 'fixed' else [FIXED_RISK_PER_TRADE]
        for risk_percent, fixed_risk in itertools.product(percent_options, fixed_options):
            risk_combos.append(
                {
                    'risk_mode': str(risk_mode),
                    'risk_percent': float(risk_percent),
                    'fixed_risk': float(fixed_risk),
                }
            )

    execution_combos = []
    for (
        allow_same_bar_fill,
        max_trades_per_day,
        one_signal_per_day,
        pending_touch_mode,
        cancel_opposite,
        close_day_change,
        intrabar_policy,
        fill_bar_policy,
        cost_per_trade,
        cost_points,
    ) in itertools.product(
        GRID_ALLOW_SAME_BAR_FILL_OPTIONS,
        GRID_MAX_TRADES_PER_DAY_OPTIONS,
        GRID_ONE_SIGNAL_PER_DAY_OPTIONS,
        GRID_PENDING_TOUCH_MODE_OPTIONS,
        GRID_CANCEL_PENDING_ON_OPPOSITE_BREAKOUT_OPTIONS,
        GRID_CLOSE_POSITION_ON_DAY_CHANGE_OPTIONS,
        GRID_INTRABAR_EXIT_POLICY_OPTIONS,
        GRID_FILL_BAR_EXIT_POLICY_OPTIONS,
        GRID_BACKTEST_COST_PER_TRADE_OPTIONS,
        GRID_BACKTEST_COST_POINTS_OPTIONS,
    ):
        execution_combos.append(
            {
                'allow_same_bar_fill': bool(allow_same_bar_fill),
                'max_trades_per_day': int(max_trades_per_day),
                'one_signal_per_day': bool(one_signal_per_day),
                'pending_touch_mode': str(pending_touch_mode),
                'cancel_opposite': bool(cancel_opposite),
                'close_day_change': bool(close_day_change),
                'intrabar_policy': str(intrabar_policy),
                'fill_bar_policy': str(fill_bar_policy),
                'cost_per_trade': float(cost_per_trade),
                'cost_points': float(cost_points),
            }
        )

    for (
        sl_mult,
        rr_ratio,
        range_window,
        strategy_mode,
        entry_mode,
        sl_placement,
        be_combo,
        trail_combo,
        ema_combo,
        risk_combo,
        execution_combo,
        pending_exp,
        trade_end,
    ) in itertools.product(
        GRID_SL_MULTIPLIERS,
        GRID_RR_RATIOS,
        GRID_RANGE_WINDOWS,
        strategy_mode_options,
        entry_mode_options,
        sl_placement_options,
        be_combos,
        trail_combos,
        ema_combos,
        risk_combos,
        execution_combos,
        pending_options,
        trade_end_options,
    ):
        rs_h, rs_m, re_h, re_m = range_window
        combo = {
            'sl_mult': float(sl_mult),
            'rr_ratio': float(rr_ratio),
            'range': (int(rs_h), int(rs_m), int(re_h), int(re_m)),
            'strategy_mode': str(strategy_mode),
            'entry_mode': str(entry_mode),
            'sl_placement': str(sl_placement),
            'pending_expiry': int(pending_exp),
            'trade_end': (int(trade_end[0]), int(trade_end[1])),
        }
        combo.update(be_combo)
        combo.update(trail_combo)
        combo.update(ema_combo)
        combo.update(risk_combo)
        combo.update(execution_combo)
        combos.append(combo)
    return combos


def _resolve_combo_data_cache(data, data_cache):
    if isinstance(data_cache, dict) and data_cache:
        first_key = next(iter(data_cache))
        if isinstance(first_key, tuple):
            key = (bool(EMA_FILTER_ENABLED), int(EMA_PERIOD) if EMA_FILTER_ENABLED else 0)
            resolved = data_cache.get(key, None)
            if resolved is not None:
                return resolved
            return get_data_cache(data, ema_filter_enabled=EMA_FILTER_ENABLED, ema_period=EMA_PERIOD)
    return data_cache


def _build_worker_data_cache_map(data):
    if data is None:
        return None
    ema_filter_options = GRID_EMA_FILTER_OPTIONS if isinstance(GRID_EMA_FILTER_OPTIONS, (list, tuple, set)) else [GRID_EMA_FILTER_OPTIONS]
    ema_period_options = GRID_EMA_PERIOD_OPTIONS if isinstance(GRID_EMA_PERIOD_OPTIONS, (list, tuple, set)) else [GRID_EMA_PERIOD_OPTIONS]
    cache_map = {}
    for enabled_raw in ema_filter_options:
        enabled = bool(enabled_raw)
        if enabled:
            for period in ema_period_options:
                cache_map[(True, int(period))] = get_data_cache(data, ema_filter_enabled=True, ema_period=int(period))
        else:
            cache_map[(False, 0)] = get_data_cache(data, ema_filter_enabled=False, ema_period=0)
    return cache_map


def _build_worker_runtime_config():
    config = {}
    for name, value in globals().items():
        if not name.isupper():
            continue
        try:
            pickle.dumps(value)
        except Exception:
            continue
        config[name] = value
    return config


def _apply_worker_runtime_config(config):
    if not config:
        return
    for name, value in config.items():
        globals()[name] = value
    if all(name in config for name in ['RANGE_START_HOUR', 'RANGE_START_MINUTE', 'RANGE_END_HOUR', 'RANGE_END_MINUTE']):
        set_range_window(
            config['RANGE_START_HOUR'],
            config['RANGE_START_MINUTE'],
            config['RANGE_END_HOUR'],
            config['RANGE_END_MINUTE'],
        )
    if all(name in config for name in ['TRADE_END_HOUR', 'TRADE_END_MINUTE']):
        globals()['TRADE_END_TIME'] = pd.Timestamp(
            f"{int(config['TRADE_END_HOUR']):02d}:{int(config['TRADE_END_MINUTE']):02d}:00"
        ).time()


def _snapshot_runtime_state():
    return {
        'SL_MULTIPLIER': SL_MULTIPLIER,
        'RR_RATIO': RR_RATIO,
        'STRATEGY_MODE': STRATEGY_MODE,
        'ENTRY_MODE': ENTRY_MODE,
        'SL_PLACEMENT': SL_PLACEMENT,
        'ALLOW_SAME_BAR_FILL': ALLOW_SAME_BAR_FILL,
        'MAX_TRADES_PER_DAY': MAX_TRADES_PER_DAY,
        'ONE_SIGNAL_PER_DAY': ONE_SIGNAL_PER_DAY,
        'PENDING_TOUCH_MODE': PENDING_TOUCH_MODE,
        'CANCEL_PENDING_ON_OPPOSITE_BREAKOUT': CANCEL_PENDING_ON_OPPOSITE_BREAKOUT,
        'CLOSE_POSITION_ON_DAY_CHANGE': CLOSE_POSITION_ON_DAY_CHANGE,
        'INTRABAR_EXIT_POLICY': INTRABAR_EXIT_POLICY,
        'FILL_BAR_EXIT_POLICY': FILL_BAR_EXIT_POLICY,
        'BACKTEST_COST_PER_TRADE': BACKTEST_COST_PER_TRADE,
        'BACKTEST_COST_POINTS': BACKTEST_COST_POINTS,
        'RISK_MODE': RISK_MODE,
        'RISK_PERCENT_PER_TRADE': RISK_PERCENT_PER_TRADE,
        'FIXED_RISK_PER_TRADE': FIXED_RISK_PER_TRADE,
        'BREAKEVEN_ENABLED': BREAKEVEN_ENABLED,
        'BREAKEVEN_TRIGGER_R': BREAKEVEN_TRIGGER_R,
        'BREAKEVEN_OFFSET': BREAKEVEN_OFFSET,
        'BREAKEVEN_TRIGGER_MODE': BREAKEVEN_TRIGGER_MODE,
        'BREAKEVEN_ALLOW_SAME_BAR': BREAKEVEN_ALLOW_SAME_BAR,
        'TRAILING_SL_ENABLED': TRAILING_SL_ENABLED,
        'TRAILING_SL_ACTIVATE_R': TRAILING_SL_ACTIVATE_R,
        'TRAILING_SL_DISTANCE_R': TRAILING_SL_DISTANCE_R,
        'TRAILING_SL_STEP_R': TRAILING_SL_STEP_R,
        'TRAILING_SL_TRIGGER_MODE': TRAILING_SL_TRIGGER_MODE,
        'TRAILING_SL_LOCK_BREAKEVEN': TRAILING_SL_LOCK_BREAKEVEN,
        'TRAILING_SL_CAP_AT_TP': TRAILING_SL_CAP_AT_TP,
        'EMA_FILTER_ENABLED': EMA_FILTER_ENABLED,
        'EMA_PERIOD': EMA_PERIOD,
        'RANGE_START_HOUR': RANGE_START_HOUR,
        'RANGE_START_MINUTE': RANGE_START_MINUTE,
        'RANGE_END_HOUR': RANGE_END_HOUR,
        'RANGE_END_MINUTE': RANGE_END_MINUTE,
        'PENDING_EXPIRY_BARS': PENDING_EXPIRY_BARS,
        'TRADE_END_HOUR': TRADE_END_HOUR,
        'TRADE_END_MINUTE': TRADE_END_MINUTE,
        'TRADE_END_TIME': TRADE_END_TIME,
    }


def _restore_runtime_state(state):
    globals()['SL_MULTIPLIER'] = state['SL_MULTIPLIER']
    globals()['RR_RATIO'] = state['RR_RATIO']
    globals()['STRATEGY_MODE'] = state['STRATEGY_MODE']
    globals()['ENTRY_MODE'] = state['ENTRY_MODE']
    globals()['SL_PLACEMENT'] = state['SL_PLACEMENT']
    globals()['ALLOW_SAME_BAR_FILL'] = state['ALLOW_SAME_BAR_FILL']
    globals()['MAX_TRADES_PER_DAY'] = state['MAX_TRADES_PER_DAY']
    globals()['ONE_SIGNAL_PER_DAY'] = state['ONE_SIGNAL_PER_DAY']
    globals()['PENDING_TOUCH_MODE'] = state['PENDING_TOUCH_MODE']
    globals()['CANCEL_PENDING_ON_OPPOSITE_BREAKOUT'] = state['CANCEL_PENDING_ON_OPPOSITE_BREAKOUT']
    globals()['CLOSE_POSITION_ON_DAY_CHANGE'] = state['CLOSE_POSITION_ON_DAY_CHANGE']
    globals()['INTRABAR_EXIT_POLICY'] = state['INTRABAR_EXIT_POLICY']
    globals()['FILL_BAR_EXIT_POLICY'] = state['FILL_BAR_EXIT_POLICY']
    globals()['BACKTEST_COST_PER_TRADE'] = state['BACKTEST_COST_PER_TRADE']
    globals()['BACKTEST_COST_POINTS'] = state['BACKTEST_COST_POINTS']
    globals()['RISK_MODE'] = state['RISK_MODE']
    globals()['RISK_PERCENT_PER_TRADE'] = state['RISK_PERCENT_PER_TRADE']
    globals()['FIXED_RISK_PER_TRADE'] = state['FIXED_RISK_PER_TRADE']
    globals()['BREAKEVEN_ENABLED'] = state['BREAKEVEN_ENABLED']
    globals()['BREAKEVEN_TRIGGER_R'] = state['BREAKEVEN_TRIGGER_R']
    globals()['BREAKEVEN_OFFSET'] = state['BREAKEVEN_OFFSET']
    globals()['BREAKEVEN_TRIGGER_MODE'] = state['BREAKEVEN_TRIGGER_MODE']
    globals()['BREAKEVEN_ALLOW_SAME_BAR'] = state['BREAKEVEN_ALLOW_SAME_BAR']
    globals()['TRAILING_SL_ENABLED'] = state['TRAILING_SL_ENABLED']
    globals()['TRAILING_SL_ACTIVATE_R'] = state['TRAILING_SL_ACTIVATE_R']
    globals()['TRAILING_SL_DISTANCE_R'] = state['TRAILING_SL_DISTANCE_R']
    globals()['TRAILING_SL_STEP_R'] = state['TRAILING_SL_STEP_R']
    globals()['TRAILING_SL_TRIGGER_MODE'] = state['TRAILING_SL_TRIGGER_MODE']
    globals()['TRAILING_SL_LOCK_BREAKEVEN'] = state['TRAILING_SL_LOCK_BREAKEVEN']
    globals()['TRAILING_SL_CAP_AT_TP'] = state['TRAILING_SL_CAP_AT_TP']
    globals()['EMA_FILTER_ENABLED'] = state['EMA_FILTER_ENABLED']
    globals()['EMA_PERIOD'] = state['EMA_PERIOD']
    globals()['PENDING_EXPIRY_BARS'] = state['PENDING_EXPIRY_BARS']
    globals()['TRADE_END_HOUR'] = state['TRADE_END_HOUR']
    globals()['TRADE_END_MINUTE'] = state['TRADE_END_MINUTE']
    globals()['TRADE_END_TIME'] = state['TRADE_END_TIME']
    set_range_window(
        state['RANGE_START_HOUR'],
        state['RANGE_START_MINUTE'],
        state['RANGE_END_HOUR'],
        state['RANGE_END_MINUTE'],
    )


def _combo_result_base(combo):
    rs_h, rs_m, re_h, re_m = combo.get('range', (None, None, None, None))
    trade_end = combo.get('trade_end')
    trade_end_label = None
    if trade_end is not None:
        trade_end_label = f"{int(trade_end[0]):02d}:{int(trade_end[1]):02d}"
    be_enabled = bool(combo.get('be_enabled', False))
    trail_enabled = bool(combo.get('trail_enabled', False))
    ema_enabled = bool(combo.get('ema_enabled', False))
    return {
        'SL_MULTIPLIER': float(combo.get('sl_mult', float('nan'))),
        'RR_RATIO': float(combo.get('rr_ratio', float('nan'))),
        'RANGE_START': f"{int(rs_h):02d}:{int(rs_m):02d}" if rs_h is not None else None,
        'RANGE_END': f"{int(re_h):02d}:{int(re_m):02d}" if re_h is not None else None,
        'STRATEGY_MODE': str(combo.get('strategy_mode', '')),
        'ENTRY_MODE': str(combo.get('entry_mode', '')),
        'SL_PLACEMENT': str(combo.get('sl_placement', '')),
        'BREAKEVEN_ENABLED': be_enabled,
        'BREAKEVEN_TRIGGER_R': float(combo.get('be_trigger_r', float('nan'))) if be_enabled else float('nan'),
        'BREAKEVEN_OFFSET': float(combo.get('be_offset', float('nan'))) if be_enabled else float('nan'),
        'BREAKEVEN_TRIGGER_MODE': str(combo.get('be_trigger_mode', BREAKEVEN_TRIGGER_MODE)) if be_enabled else '',
        'BREAKEVEN_ALLOW_SAME_BAR': bool(combo.get('be_allow_same_bar', BREAKEVEN_ALLOW_SAME_BAR)) if be_enabled else float('nan'),
        'TRAILING_SL_ENABLED': trail_enabled,
        'TRAILING_SL_ACTIVATE_R': float(combo.get('trail_activate_r', float('nan'))) if trail_enabled else float('nan'),
        'TRAILING_SL_DISTANCE_R': float(combo.get('trail_distance_r', float('nan'))) if trail_enabled else float('nan'),
        'TRAILING_SL_STEP_R': float(combo.get('trail_step_r', float('nan'))) if trail_enabled else float('nan'),
        'TRAILING_SL_LOCK_BREAKEVEN': bool(combo.get('trail_lock_be', TRAILING_SL_LOCK_BREAKEVEN)) if trail_enabled else float('nan'),
        'TRAILING_SL_TRIGGER_MODE': str(combo.get('trail_trigger_mode', TRAILING_SL_TRIGGER_MODE)) if trail_enabled else '',
        'TRAILING_SL_CAP_AT_TP': bool(combo.get('trail_cap_tp', TRAILING_SL_CAP_AT_TP)) if trail_enabled else float('nan'),
        'EMA_FILTER_ENABLED': ema_enabled,
        'EMA_PERIOD': int(combo.get('ema_period', EMA_PERIOD)) if ema_enabled else float('nan'),
        'ALLOW_SAME_BAR_FILL': bool(combo.get('allow_same_bar_fill', ALLOW_SAME_BAR_FILL)),
        'MAX_TRADES_PER_DAY': int(combo.get('max_trades_per_day', MAX_TRADES_PER_DAY)),
        'ONE_SIGNAL_PER_DAY': bool(combo.get('one_signal_per_day', ONE_SIGNAL_PER_DAY)),
        'PENDING_TOUCH_MODE': str(combo.get('pending_touch_mode', PENDING_TOUCH_MODE)),
        'CANCEL_PENDING_ON_OPPOSITE_BREAKOUT': bool(combo.get('cancel_opposite', CANCEL_PENDING_ON_OPPOSITE_BREAKOUT)),
        'CLOSE_POSITION_ON_DAY_CHANGE': bool(combo.get('close_day_change', CLOSE_POSITION_ON_DAY_CHANGE)),
        'INTRABAR_EXIT_POLICY': str(combo.get('intrabar_policy', INTRABAR_EXIT_POLICY)),
        'FILL_BAR_EXIT_POLICY': str(combo.get('fill_bar_policy', FILL_BAR_EXIT_POLICY)),
        'RISK_MODE': str(combo.get('risk_mode', RISK_MODE)),
        'RISK_PERCENT_PER_TRADE': float(combo.get('risk_percent', RISK_PERCENT_PER_TRADE)),
        'FIXED_RISK_PER_TRADE': float(combo.get('fixed_risk', FIXED_RISK_PER_TRADE)),
        'BACKTEST_COST_PER_TRADE': float(combo.get('cost_per_trade', BACKTEST_COST_PER_TRADE)),
        'BACKTEST_COST_POINTS': float(combo.get('cost_points', BACKTEST_COST_POINTS)),
        'PENDING_EXPIRY_BARS': int(combo.get('pending_expiry')) if combo.get('pending_expiry') is not None else float('nan'),
        'TRADE_END': trade_end_label,
    }


def _combo_error_result(combo, exc):
    row = _combo_result_base(combo)
    row['_status'] = 'ERROR'
    row['_error'] = str(exc)[:300]
    return row


def _evaluate_combo_task(args):
    combo, data, data_cache = args
    original_state = _snapshot_runtime_state()
    try:
        sl_mult = combo['sl_mult']
        rr_ratio = combo['rr_ratio']
        rs_h, rs_m, re_h, re_m = combo['range']
        be_enabled = combo['be_enabled']
        be_trigger_r = combo['be_trigger_r']
        be_offset = combo['be_offset']
        be_trigger_mode = combo.get('be_trigger_mode', BREAKEVEN_TRIGGER_MODE)
        be_allow_same_bar = combo.get('be_allow_same_bar', BREAKEVEN_ALLOW_SAME_BAR)
        trail_enabled = combo['trail_enabled']
        trail_activate_r = combo['trail_activate_r']
        trail_distance_r = combo['trail_distance_r']
        trail_step_r = combo['trail_step_r']
        trail_lock_be = combo['trail_lock_be']
        trail_trigger_mode = combo.get('trail_trigger_mode', TRAILING_SL_TRIGGER_MODE)
        trail_cap_tp = combo.get('trail_cap_tp', TRAILING_SL_CAP_AT_TP)
        ema_enabled = combo['ema_enabled']
        ema_period = combo['ema_period']
        strategy_mode = combo['strategy_mode']
        entry_mode = combo['entry_mode']
        sl_placement = combo['sl_placement']

        globals()['SL_MULTIPLIER'] = float(sl_mult)
        globals()['RR_RATIO'] = float(rr_ratio)
        globals()['STRATEGY_MODE'] = str(strategy_mode)
        globals()['ENTRY_MODE'] = str(entry_mode)
        globals()['SL_PLACEMENT'] = str(sl_placement)
        globals()['BREAKEVEN_ENABLED'] = bool(be_enabled)
        globals()['BREAKEVEN_TRIGGER_R'] = float(be_trigger_r)
        globals()['BREAKEVEN_OFFSET'] = float(be_offset)
        globals()['BREAKEVEN_TRIGGER_MODE'] = str(be_trigger_mode)
        globals()['BREAKEVEN_ALLOW_SAME_BAR'] = bool(be_allow_same_bar)
        globals()['TRAILING_SL_ENABLED'] = bool(trail_enabled)
        globals()['TRAILING_SL_ACTIVATE_R'] = float(trail_activate_r)
        globals()['TRAILING_SL_DISTANCE_R'] = float(trail_distance_r)
        globals()['TRAILING_SL_STEP_R'] = float(trail_step_r)
        globals()['TRAILING_SL_LOCK_BREAKEVEN'] = bool(trail_lock_be)
        globals()['TRAILING_SL_TRIGGER_MODE'] = str(trail_trigger_mode)
        globals()['TRAILING_SL_CAP_AT_TP'] = bool(trail_cap_tp)
        globals()['EMA_FILTER_ENABLED'] = bool(ema_enabled)
        globals()['EMA_PERIOD'] = int(ema_period)
        globals()['ALLOW_SAME_BAR_FILL'] = bool(combo.get('allow_same_bar_fill', ALLOW_SAME_BAR_FILL))
        globals()['MAX_TRADES_PER_DAY'] = int(combo.get('max_trades_per_day', MAX_TRADES_PER_DAY))
        globals()['ONE_SIGNAL_PER_DAY'] = bool(combo.get('one_signal_per_day', ONE_SIGNAL_PER_DAY))
        globals()['PENDING_TOUCH_MODE'] = str(combo.get('pending_touch_mode', PENDING_TOUCH_MODE))
        globals()['CANCEL_PENDING_ON_OPPOSITE_BREAKOUT'] = bool(combo.get('cancel_opposite', CANCEL_PENDING_ON_OPPOSITE_BREAKOUT))
        globals()['CLOSE_POSITION_ON_DAY_CHANGE'] = bool(combo.get('close_day_change', CLOSE_POSITION_ON_DAY_CHANGE))
        globals()['INTRABAR_EXIT_POLICY'] = str(combo.get('intrabar_policy', INTRABAR_EXIT_POLICY))
        globals()['FILL_BAR_EXIT_POLICY'] = str(combo.get('fill_bar_policy', FILL_BAR_EXIT_POLICY))
        globals()['RISK_MODE'] = str(combo.get('risk_mode', RISK_MODE))
        globals()['RISK_PERCENT_PER_TRADE'] = float(combo.get('risk_percent', RISK_PERCENT_PER_TRADE))
        globals()['FIXED_RISK_PER_TRADE'] = float(combo.get('fixed_risk', FIXED_RISK_PER_TRADE))
        globals()['BACKTEST_COST_PER_TRADE'] = float(combo.get('cost_per_trade', BACKTEST_COST_PER_TRADE))
        globals()['BACKTEST_COST_POINTS'] = float(combo.get('cost_points', BACKTEST_COST_POINTS))
        set_range_window(rs_h, rs_m, re_h, re_m)

        pending_expiry = combo.get('pending_expiry', PENDING_EXPIRY_BARS)
        trade_end = combo.get('trade_end', (TRADE_END_HOUR, TRADE_END_MINUTE))
        overrides = {}
        if pending_expiry is not None:
            overrides['PENDING_EXPIRY_BARS'] = int(pending_expiry)
            globals()['PENDING_EXPIRY_BARS'] = int(pending_expiry)
        if trade_end is not None:
            th, tm = trade_end
            overrides['TRADE_END_HOUR'] = int(th)
            overrides['TRADE_END_MINUTE'] = int(tm)
            globals()['TRADE_END_HOUR'] = int(th)
            globals()['TRADE_END_MINUTE'] = int(tm)
            globals()['TRADE_END_TIME'] = pd.Timestamp(f"{int(th):02d}:{int(tm):02d}:00").time()

        validate_config()

        combo_cache = _resolve_combo_data_cache(data, data_cache) if isinstance(data_cache, dict) else data_cache
        trades_df = run_backtest(
            data,
            silent=True,
            save_trades=False,
            show_plots=False,
            run_distribution=GRID_INCLUDE_DISTRIBUTION,
            run_curve_check=False,
            run_window_robustness=False,
            data_cache=combo_cache,
            overrides=overrides,
        )
        stats = compute_trade_stats(trades_df)
        final_balance = STARTING_BALANCE + stats['total_pnl']

        split_stats = None
        if GRID_SPLIT_ENABLED:
            split_stats = compute_grid_split_stats(trades_df, GRID_SPLIT_TRAIN_PCT)

        rand_pf_min = None
        if GRID_RANDOM_ENABLED:
            rand_pfs = []
            for (rs2_h, rs2_m, re2_h, re2_m) in GRID_RANDOM_RANGE_WINDOWS:
                set_range_window(rs2_h, rs2_m, re2_h, re2_m)
                try:
                    validate_config()
                except Exception:
                    continue
                trades_rand = run_backtest(
                    data,
                    silent=True,
                    save_trades=False,
                    show_plots=False,
                    run_distribution=False,
                    run_curve_check=False,
                    run_window_robustness=False,
                    data_cache=combo_cache,
                    overrides=overrides,
                )
                stats_rand = compute_trade_stats(trades_rand)
                rand_pfs.append(stats_rand['profit_factor'])
            set_range_window(rs_h, rs_m, re_h, re_m)
            if rand_pfs:
                rand_pf_min = min(rand_pfs)

        mc_stats = None
        if GRID_MC_ENABLED:
            mc_stats = monte_carlo_dd(trades_df, GRID_MC_ITER, GRID_MC_SEED)

        wf_stats = None
        if WF_ENABLED:
            wf_stats = walk_forward_oos_pf_pct(
                trades_df,
                WF_TRAIN_PCT,
                WF_TEST_PCT,
                WF_STEP_PCT,
                WF_PF_MIN,
            )

        prop_eval = None
        if PROP_ENABLED and PROP_GRID_ENABLED:
            target = _resolve_prop_value(PROP_PROFIT_TARGET_PCT, PROP_PROFIT_TARGET_ABS, STARTING_BALANCE)
            max_dd = _resolve_prop_value(PROP_MAX_DD_PCT, PROP_MAX_DD_ABS, STARTING_BALANCE)
            daily_dd = _resolve_prop_value(PROP_DAILY_DD_PCT, PROP_DAILY_DD_ABS, STARTING_BALANCE)
            prop_eval = evaluate_prop_path(
                trades_df,
                STARTING_BALANCE,
                target,
                max_dd,
                PROP_MAX_DD_MODE,
                daily_dd,
                PROP_DAILY_DD_MODE,
            )

        row = _combo_result_base(combo)
        row.update(
            {
                '_status': 'OK',
                'total_trades': stats['total_trades'],
                'win_rate': stats['win_rate'],
                'profit_factor': stats['profit_factor'],
                'sharpe': stats['sharpe'],
                'total_pnl': stats['total_pnl'],
                'final_balance': final_balance,
                'max_drawdown_pct': stats['max_drawdown_pct'],
                'robustness_score': float('nan'),
                'split_date': split_stats['split_date'] if split_stats else None,
                'is_trades': split_stats['is_trades'] if split_stats else 0,
                'oos_trades': split_stats['oos_trades'] if split_stats else 0,
                'oos_profit_factor': split_stats['oos_profit_factor'] if split_stats else float('nan'),
                'oos_win_rate': split_stats['oos_win_rate'] if split_stats else float('nan'),
                'oos_total_pnl': split_stats['oos_total_pnl'] if split_stats else float('nan'),
                'wf_folds': wf_stats['folds'] if wf_stats else 0,
                'wf_median_pf': wf_stats['median_pf'] if wf_stats else float('nan'),
                'wf_min_pf': wf_stats['min_pf'] if wf_stats else float('nan'),
                'wf_pct_below_min': wf_stats['pct_below_min'] if wf_stats else float('nan'),
                'prop_outcome': prop_eval['result'] if prop_eval else None,
                'prop_pass': 1 if prop_eval and prop_eval['result'] == 'PASS_TARGET' else 0,
                'rand_pf_min': rand_pf_min if rand_pf_min is not None else float('nan'),
                'mc_dd_p50': mc_stats['p50'] if mc_stats else float('nan'),
                'mc_dd_p95': mc_stats['p95'] if mc_stats else float('nan'),
                'mc_dd_p99': mc_stats['p99'] if mc_stats else float('nan'),
                'mc_dd_max': mc_stats['max'] if mc_stats else float('nan'),
            }
        )
        return row
    except Exception as exc:
        return _combo_error_result(combo, exc)
    finally:
        _restore_runtime_state(original_state)


_WORKER_GRID_DATA = None
_WORKER_GRID_CACHE = None


def _initialize_grid_worker(data, data_cache, runtime_config=None):
    global _WORKER_GRID_DATA, _WORKER_GRID_CACHE
    _apply_worker_runtime_config(runtime_config)
    _WORKER_GRID_DATA = data
    _WORKER_GRID_CACHE = data_cache


def _evaluate_combo_task_worker(combo):
    return _evaluate_combo_task((combo, _WORKER_GRID_DATA, _WORKER_GRID_CACHE))


def run_grid_search(data):
    original = {
        'SL_MULTIPLIER': SL_MULTIPLIER,
        'RR_RATIO': RR_RATIO,
        'STRATEGY_MODE': STRATEGY_MODE,
        'ENTRY_MODE': ENTRY_MODE,
        'SL_PLACEMENT': SL_PLACEMENT,
        'ALLOW_SAME_BAR_FILL': ALLOW_SAME_BAR_FILL,
        'MAX_TRADES_PER_DAY': MAX_TRADES_PER_DAY,
        'ONE_SIGNAL_PER_DAY': ONE_SIGNAL_PER_DAY,
        'PENDING_TOUCH_MODE': PENDING_TOUCH_MODE,
        'CANCEL_PENDING_ON_OPPOSITE_BREAKOUT': CANCEL_PENDING_ON_OPPOSITE_BREAKOUT,
        'CLOSE_POSITION_ON_DAY_CHANGE': CLOSE_POSITION_ON_DAY_CHANGE,
        'INTRABAR_EXIT_POLICY': INTRABAR_EXIT_POLICY,
        'FILL_BAR_EXIT_POLICY': FILL_BAR_EXIT_POLICY,
        'BACKTEST_COST_PER_TRADE': BACKTEST_COST_PER_TRADE,
        'BACKTEST_COST_POINTS': BACKTEST_COST_POINTS,
        'RISK_MODE': RISK_MODE,
        'RISK_PERCENT_PER_TRADE': RISK_PERCENT_PER_TRADE,
        'FIXED_RISK_PER_TRADE': FIXED_RISK_PER_TRADE,
        'RANGE_START_HOUR': RANGE_START_HOUR,
        'RANGE_START_MINUTE': RANGE_START_MINUTE,
        'RANGE_END_HOUR': RANGE_END_HOUR,
        'RANGE_END_MINUTE': RANGE_END_MINUTE,
        'BREAKEVEN_ENABLED': BREAKEVEN_ENABLED,
        'BREAKEVEN_TRIGGER_R': BREAKEVEN_TRIGGER_R,
        'BREAKEVEN_OFFSET': BREAKEVEN_OFFSET,
        'BREAKEVEN_TRIGGER_MODE': BREAKEVEN_TRIGGER_MODE,
        'BREAKEVEN_ALLOW_SAME_BAR': BREAKEVEN_ALLOW_SAME_BAR,
        'TRAILING_SL_ENABLED': TRAILING_SL_ENABLED,
        'TRAILING_SL_ACTIVATE_R': TRAILING_SL_ACTIVATE_R,
        'TRAILING_SL_DISTANCE_R': TRAILING_SL_DISTANCE_R,
        'TRAILING_SL_STEP_R': TRAILING_SL_STEP_R,
        'TRAILING_SL_TRIGGER_MODE': TRAILING_SL_TRIGGER_MODE,
        'TRAILING_SL_LOCK_BREAKEVEN': TRAILING_SL_LOCK_BREAKEVEN,
        'TRAILING_SL_CAP_AT_TP': TRAILING_SL_CAP_AT_TP,
        'EMA_FILTER_ENABLED': EMA_FILTER_ENABLED,
        'EMA_PERIOD': EMA_PERIOD,
        'PENDING_EXPIRY_BARS': PENDING_EXPIRY_BARS,
        'TRADE_END_HOUR': TRADE_END_HOUR,
        'TRADE_END_MINUTE': TRADE_END_MINUTE,
    }

    results = []
    error_results = []
    combos = _build_grid_param_combos()
    total_runs = len(combos)
    if total_runs == 0:
        print(
            "GRID SEARCH: No valid parameter combos. "
            "Check BE offset/trigger rules and grid option lists."
        )
        return None
    if GRID_PROGRESS_ENABLED:
        print(f"GRID SEARCH: valid combos prepared {total_runs}")
    grid_start = time.time() if GRID_PROGRESS_ENABLED else None

    # Optionally prepare a reusable data cache map to avoid recomputing it in workers.
    worker_data_cache = _build_worker_data_cache_map(data) if GRID_USE_DATA_CACHE_FOR_WORKERS else None
    worker_runtime_config = _build_worker_runtime_config()

    if GRID_PARALLEL_ENABLED:
        workers = GRID_WORKERS if GRID_WORKERS is not None else max(1, (_mp.cpu_count() - 1))
        if workers < 1:
            workers = 1
        args_iter = combos
        chunksize = max(1, total_runs // (workers * 4))
        try:
            with _mp.Pool(
                processes=workers,
                initializer=_initialize_grid_worker,
                initargs=(data, worker_data_cache, worker_runtime_config),
            ) as pool:
                completed = 0
                for res in pool.imap_unordered(_evaluate_combo_task_worker, args_iter, chunksize=chunksize):
                    completed += 1
                    if GRID_PROGRESS_ENABLED:
                        elapsed = time.time() - grid_start if grid_start else 0.0
                        rate = elapsed / completed if completed > 0 else 0.0
                        eta_min = (rate * (total_runs - completed)) / 60.0 if rate > 0 else 0.0
                        pct = (completed / total_runs) * 100.0 if total_runs else 100.0
                        print(f"\rGrid progress: {completed}/{total_runs} ({pct:.1f}%) ETA {eta_min:.1f}m", end="", flush=True)
                    if res:
                        if res.get('_status') == 'OK':
                            results.append(res)
                        else:
                            error_results.append(res)
        except Exception as exc:
            if GRID_PROGRESS_ENABLED:
                print(f"\nGrid parallel fallback: {exc}")
            # Fall back to serial execution on any multiprocessing errors.
            results = []
            error_results = []
            for combo in combos:
                res = _evaluate_combo_task((combo, data, worker_data_cache))
                if res:
                    if res.get('_status') == 'OK':
                        results.append(res)
                    else:
                        error_results.append(res)
    else:
        run_idx = 0
        for combo in combos:
            run_idx += 1
            if GRID_PROGRESS_ENABLED:
                elapsed = time.time() - grid_start if grid_start else 0.0
                rate = elapsed / run_idx if run_idx > 0 else 0.0
                eta_min = (rate * (total_runs - run_idx)) / 60.0 if rate > 0 else 0.0
                pct = (run_idx / total_runs) * 100.0 if total_runs else 100.0
                print(f"\rGrid progress: {run_idx}/{total_runs} ({pct:.1f}%) ETA {eta_min:.1f}m", end="", flush=True)
            res = _evaluate_combo_task((combo, data, worker_data_cache))
            if res:
                if res.get('_status') == 'OK':
                    results.append(res)
                else:
                    error_results.append(res)

    # Restore original config
    globals()['SL_MULTIPLIER'] = original['SL_MULTIPLIER']
    globals()['RR_RATIO'] = original['RR_RATIO']
    globals()['STRATEGY_MODE'] = original['STRATEGY_MODE']
    globals()['ENTRY_MODE'] = original['ENTRY_MODE']
    globals()['SL_PLACEMENT'] = original['SL_PLACEMENT']
    globals()['ALLOW_SAME_BAR_FILL'] = original['ALLOW_SAME_BAR_FILL']
    globals()['MAX_TRADES_PER_DAY'] = original['MAX_TRADES_PER_DAY']
    globals()['ONE_SIGNAL_PER_DAY'] = original['ONE_SIGNAL_PER_DAY']
    globals()['PENDING_TOUCH_MODE'] = original['PENDING_TOUCH_MODE']
    globals()['CANCEL_PENDING_ON_OPPOSITE_BREAKOUT'] = original['CANCEL_PENDING_ON_OPPOSITE_BREAKOUT']
    globals()['CLOSE_POSITION_ON_DAY_CHANGE'] = original['CLOSE_POSITION_ON_DAY_CHANGE']
    globals()['INTRABAR_EXIT_POLICY'] = original['INTRABAR_EXIT_POLICY']
    globals()['FILL_BAR_EXIT_POLICY'] = original['FILL_BAR_EXIT_POLICY']
    globals()['BACKTEST_COST_PER_TRADE'] = original['BACKTEST_COST_PER_TRADE']
    globals()['BACKTEST_COST_POINTS'] = original['BACKTEST_COST_POINTS']
    globals()['RISK_MODE'] = original['RISK_MODE']
    globals()['RISK_PERCENT_PER_TRADE'] = original['RISK_PERCENT_PER_TRADE']
    globals()['FIXED_RISK_PER_TRADE'] = original['FIXED_RISK_PER_TRADE']
    globals()['BREAKEVEN_ENABLED'] = original['BREAKEVEN_ENABLED']
    globals()['BREAKEVEN_TRIGGER_R'] = original['BREAKEVEN_TRIGGER_R']
    globals()['BREAKEVEN_OFFSET'] = original['BREAKEVEN_OFFSET']
    globals()['BREAKEVEN_TRIGGER_MODE'] = original['BREAKEVEN_TRIGGER_MODE']
    globals()['BREAKEVEN_ALLOW_SAME_BAR'] = original['BREAKEVEN_ALLOW_SAME_BAR']
    globals()['TRAILING_SL_ENABLED'] = original['TRAILING_SL_ENABLED']
    globals()['TRAILING_SL_ACTIVATE_R'] = original['TRAILING_SL_ACTIVATE_R']
    globals()['TRAILING_SL_DISTANCE_R'] = original['TRAILING_SL_DISTANCE_R']
    globals()['TRAILING_SL_STEP_R'] = original['TRAILING_SL_STEP_R']
    globals()['TRAILING_SL_TRIGGER_MODE'] = original['TRAILING_SL_TRIGGER_MODE']
    globals()['TRAILING_SL_LOCK_BREAKEVEN'] = original['TRAILING_SL_LOCK_BREAKEVEN']
    globals()['TRAILING_SL_CAP_AT_TP'] = original['TRAILING_SL_CAP_AT_TP']
    globals()['EMA_FILTER_ENABLED'] = original['EMA_FILTER_ENABLED']
    globals()['EMA_PERIOD'] = original['EMA_PERIOD']
    globals()['PENDING_EXPIRY_BARS'] = original['PENDING_EXPIRY_BARS']
    globals()['TRADE_END_HOUR'] = original['TRADE_END_HOUR']
    globals()['TRADE_END_MINUTE'] = original['TRADE_END_MINUTE']
    globals()['TRADE_END_TIME'] = pd.Timestamp(
        f"{int(original['TRADE_END_HOUR']):02d}:{int(original['TRADE_END_MINUTE']):02d}:00"
    ).time()
    set_range_window(
        original['RANGE_START_HOUR'],
        original['RANGE_START_MINUTE'],
        original['RANGE_END_HOUR'],
        original['RANGE_END_MINUTE'],
    )

    if GRID_PROGRESS_ENABLED:
        print()

    if not results:
        invalid_count = total_runs - len(results)
        print("GRID SEARCH: No results.")
        if invalid_count > 0:
            print(f"Invalid or failed combos: {invalid_count}/{total_runs}")
        if error_results:
            error_df = pd.DataFrame(error_results)
            top_errors = error_df['_error'].value_counts().head(5)
            print("Top combo errors:")
            for message, count in top_errors.items():
                print(f"  {count}x {message}")
        return None

    results_df = pd.DataFrame(results)
    if error_results:
        print(f"GRID SEARCH: skipped failed combos {len(error_results)}/{total_runs}")
        error_df = pd.DataFrame(error_results)
        if '_error' in error_df.columns:
            top_errors = error_df['_error'].value_counts().head(3)
            for message, count in top_errors.items():
                print(f"  {count}x {message}")
    results_df['edge_score'] = results_df.apply(compute_edge_score_from_row, axis=1)

    # Robustness evaluation is controlled from one place:
    # - GRID_ROBUSTNESS_ENABLED=False -> fully off.
    # - GRID_ROBUSTNESS_ENABLED=True  -> run eval.
    needs_robustness = bool(GRID_ROBUSTNESS_ENABLED)
    if needs_robustness and not results_df.empty:
        candidate_df = sort_grid_results_for_metric(results_df, 'edge_score')
        shortlist = candidate_df.head(min(len(candidate_df), GRID_ROBUSTNESS_SHORTLIST_SIZE))
        shortlisted_idx = shortlist.index.tolist()
        if GRID_PROGRESS_ENABLED and len(shortlisted_idx) < len(results_df):
            print(
                f"Robustness shortlist: {len(shortlisted_idx)}/{len(results_df)} "
                "top edge_score rows"
            )

        rb_total = len(shortlisted_idx)
        rb_start = time.time()
        for rb_i, idx in enumerate(shortlisted_idx, start=1):
            row = results_df.loc[idx]
            if GRID_PROGRESS_ENABLED:
                rb_elapsed = time.time() - rb_start
                rb_rate = rb_elapsed / rb_i if rb_i > 0 else 0.0
                rb_eta_min = (rb_rate * (rb_total - rb_i)) / 60.0 if rb_rate > 0 else 0.0
                rb_pct = (rb_i / rb_total) * 100.0 if rb_total else 100.0
                print(f"\rRobustness eval: {rb_i}/{rb_total} ({rb_pct:.1f}%) ETA {rb_eta_min:.1f}m", end="", flush=True)

            try:
                globals()['SL_MULTIPLIER'] = float(row['SL_MULTIPLIER'])
                globals()['RR_RATIO'] = float(row['RR_RATIO'])
                globals()['STRATEGY_MODE'] = str(row['STRATEGY_MODE'])
                globals()['ENTRY_MODE'] = str(row.get('ENTRY_MODE', original['ENTRY_MODE']))
                globals()['SL_PLACEMENT'] = str(row['SL_PLACEMENT'])
                row_be_enabled = bool(row['BREAKEVEN_ENABLED'])
                globals()['BREAKEVEN_ENABLED'] = row_be_enabled
                globals()['BREAKEVEN_TRIGGER_R'] = float(row['BREAKEVEN_TRIGGER_R']) if row_be_enabled else original['BREAKEVEN_TRIGGER_R']
                globals()['BREAKEVEN_OFFSET'] = float(row['BREAKEVEN_OFFSET']) if row_be_enabled else original['BREAKEVEN_OFFSET']
                row_trail_enabled = bool(row.get('TRAILING_SL_ENABLED', False))
                globals()['TRAILING_SL_ENABLED'] = row_trail_enabled
                globals()['TRAILING_SL_ACTIVATE_R'] = float(row['TRAILING_SL_ACTIVATE_R']) if row_trail_enabled else original['TRAILING_SL_ACTIVATE_R']
                globals()['TRAILING_SL_DISTANCE_R'] = float(row['TRAILING_SL_DISTANCE_R']) if row_trail_enabled else original['TRAILING_SL_DISTANCE_R']
                globals()['TRAILING_SL_STEP_R'] = float(row['TRAILING_SL_STEP_R']) if row_trail_enabled else original['TRAILING_SL_STEP_R']
                globals()['TRAILING_SL_LOCK_BREAKEVEN'] = bool(row.get('TRAILING_SL_LOCK_BREAKEVEN', original['TRAILING_SL_LOCK_BREAKEVEN'])) if row_trail_enabled else original['TRAILING_SL_LOCK_BREAKEVEN']
                row_ema_enabled = bool(row['EMA_FILTER_ENABLED'])
                globals()['EMA_FILTER_ENABLED'] = row_ema_enabled
                globals()['EMA_PERIOD'] = int(row['EMA_PERIOD']) if row_ema_enabled else original['EMA_PERIOD']
                if pd.notna(row.get('PENDING_EXPIRY_BARS')):
                    globals()['PENDING_EXPIRY_BARS'] = int(row['PENDING_EXPIRY_BARS'])
                else:
                    globals()['PENDING_EXPIRY_BARS'] = original['PENDING_EXPIRY_BARS']
                row_trade_end = row.get('TRADE_END')
                if isinstance(row_trade_end, str) and ':' in row_trade_end:
                    th, tm = [int(x) for x in row_trade_end.split(':')]
                    globals()['TRADE_END_HOUR'] = th
                    globals()['TRADE_END_MINUTE'] = tm
                    globals()['TRADE_END_TIME'] = pd.Timestamp(f"{th:02d}:{tm:02d}:00").time()
                else:
                    globals()['TRADE_END_HOUR'] = original['TRADE_END_HOUR']
                    globals()['TRADE_END_MINUTE'] = original['TRADE_END_MINUTE']
                    globals()['TRADE_END_TIME'] = pd.Timestamp(
                        f"{int(original['TRADE_END_HOUR']):02d}:{int(original['TRADE_END_MINUTE']):02d}:00"
                    ).time()

                rs_h, rs_m = [int(x) for x in str(row['RANGE_START']).split(':')]
                re_h, re_m = [int(x) for x in str(row['RANGE_END']).split(':')]
                set_range_window(rs_h, rs_m, re_h, re_m)
                validate_config()

                base_stats = {
                    'profit_factor': row.get('profit_factor', float('nan')),
                    'total_pnl': row.get('total_pnl', float('nan')),
                }
                robustness = evaluate_window_robustness(
                    data=data,
                    base_window=(rs_h, rs_m, re_h, re_m),
                    base_stats=base_stats,
                    shift_count=GRID_ROBUSTNESS_SHIFTS,
                    use_data_driven_shift=GRID_ROBUSTNESS_USE_DATA_DRIVEN_SHIFT,
                    shift_bars=GRID_ROBUSTNESS_SHIFT_BARS,
                    max_shift_minutes=GRID_ROBUSTNESS_MAX_SHIFT_MINUTES,
                    step_minutes=GRID_ROBUSTNESS_STEP_MINUTES,
                    seed=WINDOW_ROBUSTNESS_SEED,
                    min_trades=WINDOW_ROBUSTNESS_MIN_TRADES,
                    pf_pass_threshold=WINDOW_ROBUSTNESS_PF_PASS,
                )
                if robustness is not None:
                    results_df.at[idx, 'robustness_score'] = float(robustness['robustness_score'])
            except Exception:
                results_df.at[idx, 'robustness_score'] = float('nan')

        if GRID_PROGRESS_ENABLED and rb_total > 0:
            print()

        # Recompute edge score after robustness values are available.
        results_df['edge_score'] = results_df.apply(compute_edge_score_from_row, axis=1)

        # Restore base config after robustness loop.
        globals()['SL_MULTIPLIER'] = original['SL_MULTIPLIER']
        globals()['RR_RATIO'] = original['RR_RATIO']
        globals()['STRATEGY_MODE'] = original['STRATEGY_MODE']
        globals()['ENTRY_MODE'] = original['ENTRY_MODE']
        globals()['SL_PLACEMENT'] = original['SL_PLACEMENT']
        globals()['BREAKEVEN_ENABLED'] = original['BREAKEVEN_ENABLED']
        globals()['BREAKEVEN_TRIGGER_R'] = original['BREAKEVEN_TRIGGER_R']
        globals()['BREAKEVEN_OFFSET'] = original['BREAKEVEN_OFFSET']
        globals()['TRAILING_SL_ENABLED'] = original['TRAILING_SL_ENABLED']
        globals()['TRAILING_SL_ACTIVATE_R'] = original['TRAILING_SL_ACTIVATE_R']
        globals()['TRAILING_SL_DISTANCE_R'] = original['TRAILING_SL_DISTANCE_R']
        globals()['TRAILING_SL_STEP_R'] = original['TRAILING_SL_STEP_R']
        globals()['TRAILING_SL_TRIGGER_MODE'] = original['TRAILING_SL_TRIGGER_MODE']
        globals()['TRAILING_SL_LOCK_BREAKEVEN'] = original['TRAILING_SL_LOCK_BREAKEVEN']
        globals()['TRAILING_SL_CAP_AT_TP'] = original['TRAILING_SL_CAP_AT_TP']
        globals()['EMA_FILTER_ENABLED'] = original['EMA_FILTER_ENABLED']
        globals()['EMA_PERIOD'] = original['EMA_PERIOD']
        globals()['PENDING_EXPIRY_BARS'] = original.get('PENDING_EXPIRY_BARS', PENDING_EXPIRY_BARS)
        globals()['TRADE_END_HOUR'] = original.get('TRADE_END_HOUR', TRADE_END_HOUR)
        globals()['TRADE_END_MINUTE'] = original.get('TRADE_END_MINUTE', TRADE_END_MINUTE)
        globals()['TRADE_END_TIME'] = pd.Timestamp(f"{int(original.get('TRADE_END_HOUR', TRADE_END_HOUR)):02d}:{int(original.get('TRADE_END_MINUTE', TRADE_END_MINUTE)):02d}:00").time()
        set_range_window(
            original['RANGE_START_HOUR'],
            original['RANGE_START_MINUTE'],
            original['RANGE_END_HOUR'],
            original['RANGE_END_MINUTE'],
        )

    if CSV_EXPORT_ENABLED and GRID_RESULTS_CSV:
        results_df.to_csv(GRID_RESULTS_CSV, index=False)
        print(f"GRID SEARCH: saved -> {GRID_RESULTS_CSV}")
    else:
        print("GRID SEARCH: CSV export disabled.")

    output_df = results_df.copy()
    if GRID_ROBUSTNESS_ENABLED and GRID_MIN_ROBUSTNESS_SCORE is not None:
        output_df = output_df[output_df['robustness_score'] >= float(GRID_MIN_ROBUSTNESS_SCORE)]
        print(
            f"Edge filter: robustness_score >= {float(GRID_MIN_ROBUSTNESS_SCORE):.1f} "
            f"-> {len(output_df)}/{len(results_df)} rows"
        )
    elif (not GRID_ROBUSTNESS_ENABLED) and (GRID_MIN_ROBUSTNESS_SCORE is not None):
        print(
            "Grid robustness disabled: "
            "GRID_MIN_ROBUSTNESS_SCORE is ignored."
        )
    if not output_df.empty and CSV_EXPORT_ENABLED and GRID_EDGE_RESULTS_CSV:
        output_df.to_csv(GRID_EDGE_RESULTS_CSV, index=False)
        print(f"EDGE SEARCH: filtered results saved -> {GRID_EDGE_RESULTS_CSV}")
    elif not output_df.empty:
        print("EDGE SEARCH: CSV export disabled.")
    else:
        print("EDGE SEARCH: no rows passed robustness filter.")

    for metric in GRID_METRICS:
        if metric not in output_df.columns:
            continue
        if output_df.empty:
            break
        metric_values = output_df[metric]
        if metric_values.dropna().empty:
            continue
        top = sort_grid_results_for_metric(output_df, metric).head(GRID_TOP_N)
        if GRID_PRINT_STYLE == 'table':
            print_grid_top_table(top, metric)
        else:
            print_grid_top_compact(top, metric)

    return output_df


def _format_time_value(hour, minute):
    return f"{int(hour):02d}:{int(minute):02d}"


def _format_grid_windows(windows):
    parts = []
    for rs_h, rs_m, re_h, re_m in windows:
        parts.append(f"{int(rs_h):02d}:{int(rs_m):02d}-{int(re_h):02d}:{int(re_m):02d}")
    return "; ".join(parts)


def _format_time_tuple_options(options):
    if (
        isinstance(options, tuple)
        and len(options) == 2
        and not isinstance(options[0], (list, tuple, set))
    ):
        options = [options]
    return "; ".join(_format_time_value(hour, minute) for hour, minute in options)


def _format_list_value(values):
    if isinstance(values, (list, tuple, set)):
        return ", ".join(str(v) for v in values)
    return str(values)


def _parse_bool_text(text):
    normalized = str(text).strip().lower()
    if normalized in {'1', 'true', 'yes', 'y', 'on', 'evet'}:
        return True
    if normalized in {'0', 'false', 'no', 'n', 'off', 'hayir'}:
        return False
    raise ValueError(f"Invalid boolean value: {text}")


def _split_option_text(text):
    cleaned = str(text).strip()
    if cleaned.startswith('[') and cleaned.endswith(']'):
        cleaned = cleaned[1:-1]
    return [part.strip() for part in re.split(r"[,;]", cleaned) if part.strip()]


def _parse_list_text(text, value_type):
    parts = _split_option_text(text)
    if not parts:
        raise ValueError("List value cannot be empty.")
    if value_type == 'float':
        return [float(part) for part in parts]
    if value_type == 'int':
        return [int(float(part)) for part in parts]
    if value_type == 'bool':
        return [_parse_bool_text(part) for part in parts]
    return parts


def _parse_time_text(text):
    match = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$", str(text))
    if not match:
        raise ValueError(f"Invalid time '{text}'. Use HH:MM, e.g. 16:20.")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time '{text}'. Hour must be 0-23 and minute 0-59.")
    return hour, minute


def _parse_time_list_text(text):
    parts = [part.strip() for part in re.split(r"[;,]", str(text)) if part.strip()]
    if not parts:
        raise ValueError("Time list cannot be empty.")
    return [_parse_time_text(part) for part in parts]


def _parse_window_list_text(text):
    windows = []
    for part in [p.strip() for p in str(text).split(';') if p.strip()]:
        if '-' not in part:
            raise ValueError(f"Invalid window '{part}'. Use HH:MM-HH:MM.")
        start_text, end_text = part.split('-', 1)
        start_h, start_m = _parse_time_text(start_text)
        end_h, end_m = _parse_time_text(end_text)
        windows.append((start_h, start_m, end_h, end_m))
    if not windows:
        raise ValueError("Grid range windows cannot be empty.")
    return windows


def _write_gui_config_file(config):
    config_dir = Path(tempfile.gettempdir()) / 'ndx_strategy_gui'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"strategy_config_{int(time.time())}.json"
    with open(config_path, 'w', encoding='utf-8') as config_file:
        json.dump(config, config_file, indent=2)
    return config_path


def _open_run_in_new_terminal(mode, config):
    config_path = _write_gui_config_file(config)
    script_path = Path(__file__).resolve()
    args = [
        sys.executable,
        str(script_path),
        '--run-grid' if mode == 'grid' else '--run-solo',
        '--config',
        str(config_path),
        '--pause',
    ]
    creationflags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
    subprocess.Popen(
        args,
        cwd=str(script_path.parent),
        creationflags=creationflags,
    )
    return config_path


def launch_gui():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        print(f"GUI could not start: {exc}")
        print("Fallback: run with --run-solo or --run-grid from a terminal.")
        return

    root = tk.Tk()
    root.title("NDX100 Strategy Backtester")
    root.geometry("980x720")
    root.minsize(860, 620)

    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('Title.TLabel', font=('Segoe UI', 15, 'bold'))
    style.configure('Hint.TLabel', foreground='#5b6472')

    header = ttk.Frame(root, padding=(14, 12, 14, 8))
    header.pack(fill='x')
    ttk.Label(header, text="NDX100 Strategy Backtester", style='Title.TLabel').pack(anchor='w')
    ttk.Label(
        header,
        text="Parametreleri degistir, sonra solo backtest veya grid search'i ayri terminal penceresinde calistir.",
        style='Hint.TLabel',
    ).pack(anchor='w', pady=(3, 0))

    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=14, pady=(0, 10))

    def make_scroll_tab(title):
        outer = ttk.Frame(notebook)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        frame = ttk.Frame(canvas, padding=12)
        window_id = canvas.create_window((0, 0), window=frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        notebook.add(outer, text=title)
        return frame

    tabs = {
        'general': make_scroll_tab('Genel'),
        'strategy': make_scroll_tab('Strateji'),
        'management': make_scroll_tab('BE / Trail'),
        'grid': make_scroll_tab('Grid'),
    }

    field_vars = {}
    row_counters = {key: 0 for key in tabs}

    field_defs = [
        ('general', 'DATA_FILE', 'Data CSV', 'str', DATA_FILE, None, 'CSV yolu. Ornek: ohlc_data/NDX100_M5.csv'),
        ('general', 'START_DATE', 'Baslangic tarihi', 'str', START_DATE or '', None, 'Bos birakirsan tum data kullanilir. Ornek: 2023-01-01'),
        ('general', 'STARTING_BALANCE', 'Baslangic bakiye', 'float', STARTING_BALANCE, None, 'Hesap baslangic bakiyesi.'),
        ('general', 'RISK_MODE', 'Risk modu', 'choice', RISK_MODE, ['percent', 'fixed'], 'percent = bakiye yuzdesi, fixed = sabit para riski.'),
        ('general', 'RISK_PERCENT_PER_TRADE', 'Risk %', 'float', RISK_PERCENT_PER_TRADE, None, 'RISK_MODE percent ise kullanilir.'),
        ('general', 'FIXED_RISK_PER_TRADE', 'Sabit risk', 'float', FIXED_RISK_PER_TRADE, None, 'RISK_MODE fixed ise kullanilir.'),
        ('general', 'NEWS_FILTER_ENABLED', 'News filter', 'bool', NEWS_FILTER_ENABLED, None, 'Acilinca Forex Factory datasina gore event gunlerini bloke eder.'),
        ('general', 'SHOW_BALANCE_PLOT', 'Balance plot', 'bool', SHOW_BALANCE_PLOT, None, 'Solo backtest sonunda equity grafigi.'),
        ('general', 'SHOW_TRADE_PLOTS', 'Trade plotlari', 'bool', SHOW_TRADE_PLOTS, None, 'Solo backtest sonunda ornek trade grafikleri.'),
        ('strategy', 'RANGE_START', 'Range baslangic', 'time', _format_time_value(RANGE_START_HOUR, RANGE_START_MINUTE), None, 'HH:MM. Ornek: 16:00'),
        ('strategy', 'RANGE_END', 'Range bitis', 'time', _format_time_value(RANGE_END_HOUR, RANGE_END_MINUTE), None, 'HH:MM. Ornek: 16:20'),
        ('strategy', 'TRADE_END', 'Trade bitis', 'time', _format_time_value(TRADE_END_HOUR, TRADE_END_MINUTE), None, 'Acilan pozisyon bu saatten sonra kapatilir.'),
        ('strategy', 'STRATEGY_MODE', 'Strateji modu', 'choice', STRATEGY_MODE, ['normal', 'inverse'], 'normal: ust kirilim LONG, alt kirilim SHORT.'),
        ('strategy', 'ENTRY_MODE', 'Entry modu', 'choice', ENTRY_MODE, ['normal', 'equilibrium', 'market'], 'normal: range siniri, equilibrium: range ortasi limit, market: market emri.'),
        ('strategy', 'SL_PLACEMENT', 'SL hesabi', 'choice', SL_PLACEMENT, ['range', 'distance'], 'range: range boyu, distance: breakout mum boyu.'),
        ('strategy', 'SL_MULTIPLIER', 'SL multiplier', 'float', SL_MULTIPLIER, None, 'SL mesafesi carpanidir.'),
        ('strategy', 'RR_RATIO', 'RR ratio', 'float', RR_RATIO, None, 'TP = SL mesafesi x RR.'),
        ('strategy', 'PENDING_EXPIRY_BARS', 'Limit expiry bar', 'int', PENDING_EXPIRY_BARS, None, 'Limit emir kac bar sonra iptal olur.'),
        ('strategy', 'MAX_TRADES_PER_DAY', 'Gunluk max trade', 'int', MAX_TRADES_PER_DAY, None, 'Bir gunde en fazla kac trade.'),
        ('strategy', 'EMA_FILTER_ENABLED', 'EMA filter', 'bool', EMA_FILTER_ENABLED, None, 'LONG entry EMA ustu, SHORT EMA alti olmalidir.'),
        ('strategy', 'EMA_PERIOD', 'EMA period', 'int', EMA_PERIOD, None, 'EMA filtre periyodu.'),
        ('management', 'BREAKEVEN_ENABLED', 'Break-even', 'bool', BREAKEVEN_ENABLED, None, 'Fiyat belirli R kadar giderse SL entry yakinine tasinir.'),
        ('management', 'BREAKEVEN_TRIGGER_R', 'BE trigger R', 'float', BREAKEVEN_TRIGGER_R, None, 'Ornek: 0.5 = +0.5R hareketten sonra BE tetiklenir.'),
        ('management', 'BREAKEVEN_OFFSET', 'BE offset R', 'float', BREAKEVEN_OFFSET, None, 'Triggerdan kucuk olmali. Ornek: trigger 1.0, offset 0.25.'),
        ('management', 'BREAKEVEN_TRIGGER_MODE', 'BE trigger mode', 'choice', BREAKEVEN_TRIGGER_MODE, ['wick', 'close'], 'wick high/low kullanir, close sadece kapanis kullanir.'),
        ('management', 'BREAKEVEN_ALLOW_SAME_BAR', 'BE same bar', 'bool', BREAKEVEN_ALLOW_SAME_BAR, None, 'False daha muhafazakar OHLC varsayimidir.'),
        ('management', 'TRAILING_SL_ENABLED', 'Trailing SL', 'bool', TRAILING_SL_ENABLED, None, 'Aktif olunca SL kar yonunde bar kapanisindan sonra tasinir.'),
        ('management', 'TRAILING_SL_ACTIVATE_R', 'Trail activate R', 'float', TRAILING_SL_ACTIVATE_R, None, 'Ornek: 1.0 = fiyat +1R gittikten sonra trail baslar.'),
        ('management', 'TRAILING_SL_DISTANCE_R', 'Trail distance R', 'float', TRAILING_SL_DISTANCE_R, None, 'Stop, secilen high/low veya close arkasinda bu kadar R durur.'),
        ('management', 'TRAILING_SL_STEP_R', 'Trail step R', 'float', TRAILING_SL_STEP_R, None, 'Stop en az bu kadar iyilesirse tasinir. 0 = her iyilesme.'),
        ('management', 'TRAILING_SL_TRIGGER_MODE', 'Trail mode', 'choice', TRAILING_SL_TRIGGER_MODE, ['wick', 'close'], 'wick daha hizli, close daha muhafazakar sinyal verir.'),
        ('management', 'TRAILING_SL_LOCK_BREAKEVEN', 'Trail BE kilidi', 'bool', TRAILING_SL_LOCK_BREAKEVEN, None, 'Trail aktifken stopu entry seviyesinden kotuye dusurmez.'),
        ('management', 'TRAILING_SL_CAP_AT_TP', 'Trail TP cap', 'bool', TRAILING_SL_CAP_AT_TP, None, 'Trailing stop TP seviyesini gecmesin.'),
        ('grid', 'GRID_SL_MULTIPLIERS', 'Grid SL listesi', 'float_list', _format_list_value(GRID_SL_MULTIPLIERS), None, 'Virgulle ayir. Ornek: 1.0, 1.5, 2.0'),
        ('grid', 'GRID_RR_RATIOS', 'Grid RR listesi', 'float_list', _format_list_value(GRID_RR_RATIOS), None, 'Virgulle ayir. Ornek: 0.5, 1.0, 2.0'),
        ('grid', 'GRID_RANGE_WINDOWS', 'Grid range windows', 'window_list', _format_grid_windows(GRID_RANGE_WINDOWS), None, 'Noktali virgul ayir. Ornek: 16:00-16:20; 15:30-16:00'),
        ('grid', 'GRID_BREAKEVEN_ENABLED_OPTIONS', 'Grid BE on/off', 'bool_list', _format_list_value(GRID_BREAKEVEN_ENABLED_OPTIONS), None, 'Ornek: True, False'),
        ('grid', 'GRID_BREAKEVEN_TRIGGER_R_OPTIONS', 'Grid BE trigger', 'float_list', _format_list_value(GRID_BREAKEVEN_TRIGGER_R_OPTIONS), None, 'Offset triggerdan kucuk olmayan kombinasyonlar otomatik atlanir.'),
        ('grid', 'GRID_BREAKEVEN_OFFSET_OPTIONS', 'Grid BE offset', 'float_list', _format_list_value(GRID_BREAKEVEN_OFFSET_OPTIONS), None, 'Ornek: 0.0, 0.25, 0.5'),
        ('grid', 'GRID_TRAILING_SL_ENABLED_OPTIONS', 'Grid trail on/off', 'bool_list', _format_list_value(GRID_TRAILING_SL_ENABLED_OPTIONS), None, 'Ornek: False, True'),
        ('grid', 'GRID_TRAILING_SL_ACTIVATE_R_OPTIONS', 'Grid trail activate', 'float_list', _format_list_value(GRID_TRAILING_SL_ACTIVATE_R_OPTIONS), None, 'Ornek: 0.75, 1.0, 1.5'),
        ('grid', 'GRID_TRAILING_SL_DISTANCE_R_OPTIONS', 'Grid trail distance', 'float_list', _format_list_value(GRID_TRAILING_SL_DISTANCE_R_OPTIONS), None, 'Ornek: 0.5, 0.75, 1.0'),
        ('grid', 'GRID_TRAILING_SL_STEP_R_OPTIONS', 'Grid trail step', 'float_list', _format_list_value(GRID_TRAILING_SL_STEP_R_OPTIONS), None, 'Ornek: 0.0, 0.25'),
        ('grid', 'GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS', 'Grid trail BE lock', 'bool_list', _format_list_value(GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS), None, 'Ornek: True veya True, False'),
        ('grid', 'GRID_EMA_FILTER_OPTIONS', 'Grid EMA on/off', 'bool_list', _format_list_value(GRID_EMA_FILTER_OPTIONS), None, 'Ornek: False, True'),
        ('grid', 'GRID_EMA_PERIOD_OPTIONS', 'Grid EMA period', 'int_list', _format_list_value(GRID_EMA_PERIOD_OPTIONS), None, 'Ornek: 100, 200'),
        ('grid', 'GRID_PENDING_EXPIRY_BARS_OPTIONS', 'Grid expiry', 'int_list', _format_list_value(GRID_PENDING_EXPIRY_BARS_OPTIONS), None, 'Ornek: 5, 10, 15'),
        ('grid', 'GRID_TRADE_END_OPTIONS', 'Grid trade end', 'time_list', _format_time_tuple_options(GRID_TRADE_END_OPTIONS), None, 'Ornek: 21:00; 22:00'),
        ('grid', 'GRID_TOP_N', 'Grid top N', 'int', GRID_TOP_N, None, 'Her metrikte kac satir basilsin.'),
        ('grid', 'GRID_PARALLEL_ENABLED', 'Grid paralel', 'bool', GRID_PARALLEL_ENABLED, None, 'CPU cekirdekleriyle hizli grid.'),
        ('grid', 'GRID_WORKERS', 'Grid worker', 'optional_int', '' if GRID_WORKERS is None else GRID_WORKERS, None, 'Bos = otomatik.'),
        ('grid', 'GRID_MC_ENABLED', 'Grid Monte Carlo', 'bool', GRID_MC_ENABLED, None, 'Kapamak grid hizini artirir.'),
        ('grid', 'GRID_ROBUSTNESS_ENABLED', 'Grid robustness', 'bool', GRID_ROBUSTNESS_ENABLED, None, 'Kapamak grid hizini artirir.'),
    ]

    def add_field(tab_key, name, label, field_type, default, choices, hint):
        frame = tabs[tab_key]
        row = row_counters[tab_key]
        row_counters[tab_key] += 1
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=6)
        if field_type == 'bool':
            var = tk.BooleanVar(value=bool(default))
            widget = ttk.Checkbutton(frame, variable=var)
        elif field_type == 'choice':
            var = tk.StringVar(value=str(default))
            widget = ttk.Combobox(frame, textvariable=var, values=choices, state='readonly')
        else:
            var = tk.StringVar(value=str(default))
            widget = ttk.Entry(frame, textvariable=var)
        widget.grid(row=row, column=1, sticky='ew', pady=6)
        ttk.Label(frame, text=hint, style='Hint.TLabel', wraplength=430).grid(row=row, column=2, sticky='w', padx=(10, 0), pady=6)
        frame.columnconfigure(1, weight=1)
        field_vars[name] = (var, field_type)

    for field in field_defs:
        add_field(*field)

    def browse_data_file():
        file_path = filedialog.askopenfilename(
            title='Data CSV sec',
            filetypes=[('CSV files', '*.csv'), ('All files', '*.*')],
            initialdir=str(Path(__file__).resolve().parent),
        )
        if file_path:
            field_vars['DATA_FILE'][0].set(file_path)

    data_browse = ttk.Button(tabs['general'], text='CSV Sec', command=browse_data_file)
    data_browse.grid(row=0, column=3, padx=(8, 0), pady=6)

    def read_config_from_gui():
        config = {}
        for name, (var, field_type) in field_vars.items():
            raw_value = var.get()
            if field_type == 'bool':
                value = bool(raw_value)
            elif field_type == 'int':
                value = int(float(raw_value))
            elif field_type == 'optional_int':
                value = None if str(raw_value).strip() == '' else int(float(raw_value))
            elif field_type == 'float':
                value = float(raw_value)
            elif field_type == 'float_list':
                value = _parse_list_text(raw_value, 'float')
            elif field_type == 'int_list':
                value = _parse_list_text(raw_value, 'int')
            elif field_type == 'bool_list':
                value = _parse_list_text(raw_value, 'bool')
            elif field_type == 'time':
                value = _parse_time_text(raw_value)
            elif field_type == 'time_list':
                value = _parse_time_list_text(raw_value)
            elif field_type == 'window_list':
                value = _parse_window_list_text(raw_value)
            else:
                value = str(raw_value).strip()

            if name == 'RANGE_START':
                config['RANGE_START_HOUR'], config['RANGE_START_MINUTE'] = value
            elif name == 'RANGE_END':
                config['RANGE_END_HOUR'], config['RANGE_END_MINUTE'] = value
            elif name == 'TRADE_END':
                config['TRADE_END_HOUR'], config['TRADE_END_MINUTE'] = value
            else:
                config[name] = value

        return config

    def run_mode(mode):
        try:
            config = read_config_from_gui()
            config['GRID_SEARCH_ENABLED'] = mode == 'grid'
            config_path = _open_run_in_new_terminal(mode, config)
            messagebox.showinfo(
                'Calistirildi',
                f"{'Grid search' if mode == 'grid' else 'Solo backtest'} yeni terminalde baslatildi.\nConfig: {config_path}",
            )
        except Exception as exc:
            messagebox.showerror('Hata', str(exc))

    footer = ttk.Frame(root, padding=(14, 0, 14, 14))
    footer.pack(fill='x')
    ttk.Button(footer, text='Solo Backtest Calistir', command=lambda: run_mode('solo')).pack(side='left')
    ttk.Button(footer, text='Grid Search Calistir', command=lambda: run_mode('grid')).pack(side='left', padx=(8, 0))
    ttk.Button(footer, text='Kapat', command=root.destroy).pack(side='right')

    root.mainloop()


def launch_gui_v2():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:
        print(f"GUI could not start: {exc}")
        print("Fallback: run with --run-solo or --run-grid from a terminal.")
        return

    root = tk.Tk()
    root.title("NDX100 Strategy Control Panel")
    root.geometry("1180x760")
    root.minsize(1040, 680)

    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('Title.TLabel', font=('Segoe UI', 17, 'bold'))
    style.configure('Section.TLabelframe.Label', font=('Segoe UI', 10, 'bold'))
    style.configure('Hint.TLabel', foreground='#5b6472')
    style.configure('Small.TLabel', foreground='#6b7280', font=('Segoe UI', 8))
    style.configure('Code.TLabel', foreground='#334155', font=('Consolas', 8))
    style.configure('Run.TButton', font=('Segoe UI', 10, 'bold'))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    header = ttk.Frame(root, padding=(16, 12, 16, 8))
    header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text="NDX100 Strategy Control Panel", style='Title.TLabel').grid(row=0, column=0, sticky='w')
    ttk.Label(
        header,
        text=(
            "Koddan ayar degistirir gibi calisir, ama her ayarin ne yaptigini ve hangi degerlerin mantikli "
            "oldugunu aciklar. Solo test veya grid search yeni terminalde baslar."
        ),
        style='Hint.TLabel',
        wraplength=980,
    ).grid(row=1, column=0, sticky='w', pady=(4, 0))

    body = ttk.PanedWindow(root, orient='horizontal')
    body.grid(row=1, column=0, sticky='nsew', padx=14, pady=(0, 12))

    left = ttk.Frame(body, padding=12)
    right = ttk.Frame(body)
    body.add(left, weight=1)
    body.add(right, weight=4)
    left.columnconfigure(0, weight=1)
    left.rowconfigure(1, weight=1)

    ttk.Label(left, text="Calistirma", font=('Segoe UI', 12, 'bold')).grid(row=0, column=0, sticky='w')
    summary_box = ttk.LabelFrame(left, text="Anlik Ozet", style='Section.TLabelframe', padding=10)
    summary_box.grid(row=1, column=0, sticky='nsew', pady=(8, 10))
    summary_box.columnconfigure(0, weight=1)
    summary_text = tk.StringVar(value="Ayarlar okunuyor...")
    ttk.Label(summary_box, textvariable=summary_text, justify='left', wraplength=285).grid(row=0, column=0, sticky='nw')

    help_box = ttk.LabelFrame(left, text="Secili Ayar", style='Section.TLabelframe', padding=10)
    help_box.grid(row=2, column=0, sticky='ew', pady=(0, 10))
    help_text = tk.StringVar(value="Bir ayara tiklayinca burada aciklama ve ornek deger gorunur.")
    ttk.Label(help_box, textvariable=help_text, justify='left', wraplength=285).grid(row=0, column=0, sticky='w')

    preset_box = ttk.LabelFrame(left, text="Hazir Ayar", style='Section.TLabelframe', padding=10)
    preset_box.grid(row=3, column=0, sticky='ew', pady=(0, 10))
    preset_box.columnconfigure(0, weight=1)
    preset_box.columnconfigure(1, weight=1)

    run_box = ttk.LabelFrame(left, text="Yeni Terminal", style='Section.TLabelframe', padding=10)
    run_box.grid(row=4, column=0, sticky='ew')
    run_box.columnconfigure(0, weight=1)
    run_box.columnconfigure(1, weight=1)

    notebook = ttk.Notebook(right)
    notebook.pack(fill='both', expand=True)

    def make_tab(title, intro):
        outer = ttk.Frame(notebook)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        frame = ttk.Frame(canvas, padding=12)
        window_id = canvas.create_window((0, 0), window=frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        ttk.Label(frame, text=intro, style='Hint.TLabel', wraplength=780).grid(row=0, column=0, sticky='ew', pady=(0, 12))
        notebook.add(outer, text=title)
        return frame

    tabs = {
        'setup': make_tab('1. Temel', 'Data, tarih, risk ve cikti ayarlari. Bunlar hem solo hem grid calismasini etkiler.'),
        'strategy': make_tab('2. Strateji', 'Range penceresi, entry tarzi, SL/TP ve filtreler. Python kodundaki ana strateji ayarlari burada.'),
        'manage': make_tab('3. BE / Trail', 'R = ilk stop mesafesi. Break-even ve trailing stop davranisini buradan ayarla.'),
        'grid': make_tab('4. Grid', 'Virgulle birden fazla deger yaz. Sistem mantiksiz BE kombinasyonlarini otomatik atlar.'),
        'advanced': make_tab('5. Ileri', 'Hiz ve rapor ayarlari. Emin degilsen varsayilani koru.'),
    }

    field_vars = {}
    field_meta = {}
    default_values = {}
    section_frames = {}
    row_counters = {}
    summary_after_id = None

    fields = [
        ('setup', 'Data', 'DATA_FILE', 'Data CSV', 'str', DATA_FILE, None, 'Mum verisinin CSV yolu. Ornek: ohlc_data/NDX100_M5.csv'),
        ('setup', 'Data', 'START_DATE', 'Baslangic tarihi', 'str', START_DATE or '', None, 'Bu tarihten onceki veriyi yok sayar. Bos = tum veri. Ornek: 2023-01-01'),
        ('setup', 'Risk', 'STARTING_BALANCE', 'Baslangic bakiye', 'float', STARTING_BALANCE, None, 'Backtest equity hesabinin baslangic bakiyesi.'),
        ('setup', 'Risk', 'RISK_MODE', 'Risk modu', 'choice', RISK_MODE, ['percent', 'fixed'], 'percent = equity yuzdesi; fixed = trade basina sabit para riski.'),
        ('setup', 'Risk', 'RISK_PERCENT_PER_TRADE', 'Risk yuzdesi', 'float', RISK_PERCENT_PER_TRADE, None, 'RISK_MODE percent ise kullanilir. 1 = %1 risk.'),
        ('setup', 'Risk', 'FIXED_RISK_PER_TRADE', 'Sabit risk', 'float', FIXED_RISK_PER_TRADE, None, 'RISK_MODE fixed ise kullanilir. Ornek: 50.'),
        ('setup', 'Cikti', 'SHOW_BALANCE_PLOT', 'Equity grafigi', 'bool', SHOW_BALANCE_PLOT, None, 'Solo backtest sonunda bakiye grafigini acar.'),
        ('setup', 'Cikti', 'SHOW_TRADE_PLOTS', 'Trade grafikleri', 'bool', SHOW_TRADE_PLOTS, None, 'Solo backtest sonunda ornek trade grafiklerini acar. Grid icin kapali tutmak daha hizlidir.'),
        ('setup', 'Cikti', 'NEWS_FILTER_ENABLED', 'News filter', 'bool', NEWS_FILTER_ENABLED, None, 'Forex Factory datasina gore haber gunlerini bloke eder. Saat dilimi dogru degilse sonucu bozabilir.'),

        ('strategy', 'Zaman', 'RANGE_START', 'Range baslangic', 'time', _format_time_value(RANGE_START_HOUR, RANGE_START_MINUTE), None, 'Range high/low hesaplamasinin basladigi saat. Format HH:MM.'),
        ('strategy', 'Zaman', 'RANGE_END', 'Range bitis', 'time', _format_time_value(RANGE_END_HOUR, RANGE_END_MINUTE), None, 'Range high/low hesaplamasinin bittigi saat. Sinyal bu saatten sonra aranir.'),
        ('strategy', 'Zaman', 'TRADE_END', 'Trade bitis', 'time', _format_time_value(TRADE_END_HOUR, TRADE_END_MINUTE), None, 'Pozisyon veya pending emir bu saatten sonra kapatilir/iptal edilir.'),
        ('strategy', 'Sinyal', 'STRATEGY_MODE', 'Strateji modu', 'choice', STRATEGY_MODE, ['normal', 'inverse'], 'normal: range high ustu LONG, range low alti SHORT. inverse ters mantik.'),
        ('strategy', 'Sinyal', 'ENTRY_MODE', 'Entry modu', 'choice', ENTRY_MODE, ['normal', 'equilibrium', 'market'], 'normal: entry kirilan range sinirinda. equilibrium: ortada. market: aninda.'),
        ('strategy', 'Sinyal', 'PENDING_EXPIRY_BARS', 'Limit emir omru', 'int', PENDING_EXPIRY_BARS, None, 'Limit emir kac mum dolmazsa iptal olsun. Ornek: 5.'),
        ('strategy', 'Sinyal', 'MAX_TRADES_PER_DAY', 'Gunluk max trade', 'int', MAX_TRADES_PER_DAY, None, 'Bir gunde alinacak en fazla trade. Bu stratejide genelde 1 temizdir.'),
        ('strategy', 'SL / TP', 'SL_PLACEMENT', 'SL hesabi', 'choice', SL_PLACEMENT, ['range', 'distance'], 'range: range high-low boyu. distance: breakout mum high-low boyu.'),
        ('strategy', 'SL / TP', 'SL_MULTIPLIER', 'SL carpan', 'float', SL_MULTIPLIER, None, 'SL mesafesini carpar. Range 100 ve carpan 1.5 ise SL 150 puan.'),
        ('strategy', 'SL / TP', 'RR_RATIO', 'RR oran', 'float', RR_RATIO, None, 'TP mesafesi = SL mesafesi x RR. Ornek: 2.0 = 2R hedef.'),
        ('strategy', 'Filtre', 'EMA_FILTER_ENABLED', 'EMA filtresi', 'bool', EMA_FILTER_ENABLED, None, 'LONG icin entry EMA ustunde, SHORT icin EMA altinda olmali.'),
        ('strategy', 'Filtre', 'EMA_PERIOD', 'EMA periyot', 'int', EMA_PERIOD, None, 'EMA filtre periyodu. Ornek: 200.'),

        ('manage', 'Break-even', 'BREAKEVEN_ENABLED', 'BE aktif', 'bool', BREAKEVEN_ENABLED, None, 'Fiyat belirli R kara gidince SL entry veya entryden iyi seviyeye tasinir.'),
        ('manage', 'Break-even', 'BREAKEVEN_TRIGGER_R', 'BE trigger R', 'float', BREAKEVEN_TRIGGER_R, None, 'Kac R kar gorunce BE tetiklensin. Ornek: 0.5.'),
        ('manage', 'Break-even', 'BREAKEVEN_OFFSET', 'BE offset R', 'float', BREAKEVEN_OFFSET, None, 'SL entryden kac R iyiye tasinsin. Triggerdan kucuk olmali. Ornek: 0.0 veya 0.25.'),
        ('manage', 'Break-even', 'BREAKEVEN_TRIGGER_MODE', 'BE kaynak', 'choice', BREAKEVEN_TRIGGER_MODE, ['wick', 'close'], 'wick: high/low tetikler. close: sadece kapanis tetikler, daha muhafazakar.'),
        ('manage', 'Break-even', 'BREAKEVEN_ALLOW_SAME_BAR', 'Ayni mumda BE', 'bool', BREAKEVEN_ALLOW_SAME_BAR, None, 'False daha muhafazakar. True OHLC sirasi bilinmedigi icin daha iyimser olabilir.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_ENABLED', 'Trail aktif', 'bool', TRAILING_SL_ENABLED, None, 'Aciksa SL kar yonunde takip eder. Backtestte yeni stop bar kapandiktan sonra etkili olur.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_ACTIVATE_R', 'Trail baslama R', 'float', TRAILING_SL_ACTIVATE_R, None, 'Kac R kar gorunce trailing baslasin. Ornek: 1.0.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_DISTANCE_R', 'Trail mesafe R', 'float', TRAILING_SL_DISTANCE_R, None, 'Stop referans fiyat arkasinda kac R dursun. Kucuk deger daha siki stop demektir.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_STEP_R', 'Trail adim R', 'float', TRAILING_SL_STEP_R, None, 'Stop en az kac R iyilesirse tasinsin. 0 = her iyilesme.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_TRIGGER_MODE', 'Trail kaynak', 'choice', TRAILING_SL_TRIGGER_MODE, ['wick', 'close'], 'wick daha hizli, close daha muhafazakar.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_LOCK_BREAKEVEN', 'BE altina dusme', 'bool', TRAILING_SL_LOCK_BREAKEVEN, None, 'Trail aktif olduktan sonra SL entryden kotu seviyeye gitmez.'),
        ('manage', 'Trailing SL', 'TRAILING_SL_CAP_AT_TP', 'TP cap', 'bool', TRAILING_SL_CAP_AT_TP, None, 'Trailing stop TP seviyesini gecmesin. Genelde acik kalmali.'),

        ('grid', 'Temel listeler', 'GRID_SL_MULTIPLIERS', 'SL carpanlari', 'float_list', _format_list_value(GRID_SL_MULTIPLIERS), None, 'Virgulle ayir. Ornek: 1.0, 1.5, 2.0'),
        ('grid', 'Temel listeler', 'GRID_RR_RATIOS', 'RR listesi', 'float_list', _format_list_value(GRID_RR_RATIOS), None, 'Virgulle ayir. Ornek: 0.5, 1.0, 2.0'),
        ('grid', 'Temel listeler', 'GRID_RANGE_WINDOWS', 'Range pencereleri', 'window_list', _format_grid_windows(GRID_RANGE_WINDOWS), None, 'Noktali virgul ile ayir. Ornek: 16:00-16:20; 15:30-16:00'),
        ('grid', 'BE grid', 'GRID_BREAKEVEN_ENABLED_OPTIONS', 'BE on/off', 'bool_list', _format_list_value(GRID_BREAKEVEN_ENABLED_OPTIONS), None, 'Ornek: True, False. Ikisini de yazarsan acik/kapali denenir.'),
        ('grid', 'BE grid', 'GRID_BREAKEVEN_TRIGGER_R_OPTIONS', 'BE trigger listesi', 'float_list', _format_list_value(GRID_BREAKEVEN_TRIGGER_R_OPTIONS), None, 'Ornek: 0.5, 1.0. Offset triggerdan buyuk/esit olursa atlanir.'),
        ('grid', 'BE grid', 'GRID_BREAKEVEN_OFFSET_OPTIONS', 'BE offset listesi', 'float_list', _format_list_value(GRID_BREAKEVEN_OFFSET_OPTIONS), None, 'Ornek: 0.0, 0.25, 0.5. 0.5/0.5 gibi mantiksiz eslesme denenmez.'),
        ('grid', 'Trailing grid', 'GRID_TRAILING_SL_ENABLED_OPTIONS', 'Trail on/off', 'bool_list', _format_list_value(GRID_TRAILING_SL_ENABLED_OPTIONS), None, 'Ornek: False, True. Faydayi karsilastirmak icin ikisini de yaz.'),
        ('grid', 'Trailing grid', 'GRID_TRAILING_SL_ACTIVATE_R_OPTIONS', 'Trail baslama listesi', 'float_list', _format_list_value(GRID_TRAILING_SL_ACTIVATE_R_OPTIONS), None, 'Ornek: 0.75, 1.0, 1.5'),
        ('grid', 'Trailing grid', 'GRID_TRAILING_SL_DISTANCE_R_OPTIONS', 'Trail mesafe listesi', 'float_list', _format_list_value(GRID_TRAILING_SL_DISTANCE_R_OPTIONS), None, 'Ornek: 0.5, 0.75, 1.0'),
        ('grid', 'Trailing grid', 'GRID_TRAILING_SL_STEP_R_OPTIONS', 'Trail adim listesi', 'float_list', _format_list_value(GRID_TRAILING_SL_STEP_R_OPTIONS), None, 'Ornek: 0.0, 0.25'),
        ('grid', 'Trailing grid', 'GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS', 'Trail BE kilidi', 'bool_list', _format_list_value(GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS), None, 'Genelde True. True, False yazarsan iki davranisi dener.'),
        ('grid', 'Filtre ve zaman', 'GRID_EMA_FILTER_OPTIONS', 'EMA on/off', 'bool_list', _format_list_value(GRID_EMA_FILTER_OPTIONS), None, 'Ornek: False, True.'),
        ('grid', 'Filtre ve zaman', 'GRID_EMA_PERIOD_OPTIONS', 'EMA periyotlari', 'int_list', _format_list_value(GRID_EMA_PERIOD_OPTIONS), None, 'EMA aciksa denenir. Ornek: 100, 200.'),
        ('grid', 'Filtre ve zaman', 'GRID_PENDING_EXPIRY_BARS_OPTIONS', 'Expiry listesi', 'int_list', _format_list_value(GRID_PENDING_EXPIRY_BARS_OPTIONS), None, 'Limit emir omru listesi. Ornek: 5, 10, 15.'),
        ('grid', 'Filtre ve zaman', 'GRID_TRADE_END_OPTIONS', 'Trade end listesi', 'time_list', _format_time_tuple_options(GRID_TRADE_END_OPTIONS), None, 'Ornek: 21:00; 22:00'),

        ('advanced', 'Grid hizi', 'GRID_TOP_N', 'Top N', 'int', GRID_TOP_N, None, 'Her metrikte kac sonuc yazdirilsin.'),
        ('advanced', 'Grid hizi', 'GRID_PARALLEL_ENABLED', 'Paralel grid', 'bool', GRID_PARALLEL_ENABLED, None, 'Aciksa kombinasyonlar CPU cekirdeklerine dagitilir.'),
        ('advanced', 'Grid hizi', 'GRID_WORKERS', 'Worker sayisi', 'optional_int', '' if GRID_WORKERS is None else GRID_WORKERS, None, 'Bos = otomatik. Cok yuksek yazmak sistemi yavaslatabilir.'),
        ('advanced', 'Grid hizi', 'GRID_MC_ENABLED', 'Grid Monte Carlo', 'bool', GRID_MC_ENABLED, None, 'Aciksa grid daha yavas ama risk raporu daha detayli olur.'),
        ('advanced', 'Grid hizi', 'GRID_ROBUSTNESS_ENABLED', 'Grid robustness', 'bool', GRID_ROBUSTNESS_ENABLED, None, 'Aciksa en iyi sonuclarda zaman kaydirma testi yapar. Daha yavas.'),
        ('advanced', 'Raporlar', 'WINDOW_ROBUSTNESS_ENABLED', 'Solo robustness', 'bool', WINDOW_ROBUSTNESS_ENABLED, None, 'Solo backtestte range penceresini kaydirip stabilite raporu verir.'),
        ('advanced', 'Raporlar', 'CURVE_CHECK_ENABLED', 'Curve check', 'bool', CURVE_CHECK_ENABLED, None, 'Trade seviyesinde curve-fit kontrolu yapar.'),
        ('advanced', 'Raporlar', 'DISTRIBUTION_ANALYSIS_ENABLED', 'Distribution', 'bool', DISTRIBUTION_ANALYSIS_ENABLED, None, 'Trade sonrasi dagilim analizi. Gridde hiz icin kapatilabilir.'),
        ('advanced', 'Raporlar', 'PROP_ENABLED', 'Prop report', 'bool', PROP_ENABLED, None, 'Prop firm hedef/drawdown raporu hesaplar.'),
    ]

    def get_section(tab_key, section_name):
        key = (tab_key, section_name)
        if key in section_frames:
            return section_frames[key]
        parent = tabs[tab_key]
        row = len([existing for existing in section_frames if existing[0] == tab_key]) + 1
        frame = ttk.LabelFrame(parent, text=section_name, style='Section.TLabelframe', padding=10)
        frame.grid(row=row, column=0, sticky='ew', pady=(0, 12))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=2)
        section_frames[key] = frame
        row_counters[key] = 0
        return frame

    def set_help(name):
        meta = field_meta.get(name)
        if meta:
            help_text.set(f"{meta['label']} ({name})\n\n{meta['help']}")

    def schedule_summary_update():
        nonlocal summary_after_id
        if summary_after_id is not None:
            try:
                root.after_cancel(summary_after_id)
            except Exception:
                pass
        summary_after_id = root.after(120, update_summary)

    def add_field(spec):
        tab_key, section_name, name, label, field_type, default, choices, help_msg = spec
        frame = get_section(tab_key, section_name)
        row_key = (tab_key, section_name)
        row = row_counters[row_key]
        row_counters[row_key] += 1

        label_frame = ttk.Frame(frame)
        label_frame.grid(row=row, column=0, sticky='nw', padx=(0, 12), pady=7)
        ttk.Label(label_frame, text=label).grid(row=0, column=0, sticky='w')
        ttk.Label(label_frame, text=name, style='Code.TLabel').grid(row=1, column=0, sticky='w')

        if field_type == 'bool':
            var = tk.BooleanVar(value=bool(default))
            widget = ttk.Checkbutton(frame, variable=var)
            widget.configure(command=lambda n=name: (set_help(n), schedule_summary_update()))
        elif field_type == 'choice':
            var = tk.StringVar(value=str(default))
            widget = ttk.Combobox(frame, textvariable=var, values=choices, state='readonly')
            var.trace_add('write', lambda *_args: schedule_summary_update())
        else:
            var = tk.StringVar(value=str(default))
            widget = ttk.Entry(frame, textvariable=var)
            var.trace_add('write', lambda *_args: schedule_summary_update())

        widget.grid(row=row, column=1, sticky='ew', pady=7)
        ttk.Label(frame, text=help_msg, style='Hint.TLabel', wraplength=390).grid(row=row, column=2, sticky='w', padx=(12, 0), pady=7)
        widget.bind('<FocusIn>', lambda _event, n=name: set_help(n))
        label_frame.bind('<Button-1>', lambda _event, n=name: set_help(n))

        field_vars[name] = (var, field_type)
        field_meta[name] = {'label': label, 'help': help_msg}
        default_values[name] = default

    def set_field(name, value):
        if name not in field_vars:
            return
        var, field_type = field_vars[name]
        if field_type == 'bool':
            var.set(bool(value))
        else:
            var.set(str(value))

    def read_config_from_gui():
        config = {}
        for name, (var, field_type) in field_vars.items():
            raw_value = var.get()
            if field_type == 'bool':
                value = bool(raw_value)
            elif field_type == 'int':
                value = int(float(raw_value))
            elif field_type == 'optional_int':
                value = None if str(raw_value).strip() == '' else int(float(raw_value))
            elif field_type == 'float':
                value = float(raw_value)
            elif field_type == 'float_list':
                value = _parse_list_text(raw_value, 'float')
            elif field_type == 'int_list':
                value = _parse_list_text(raw_value, 'int')
            elif field_type == 'bool_list':
                value = _parse_list_text(raw_value, 'bool')
            elif field_type == 'time':
                value = _parse_time_text(raw_value)
            elif field_type == 'time_list':
                value = _parse_time_list_text(raw_value)
            elif field_type == 'window_list':
                value = _parse_window_list_text(raw_value)
            else:
                value = str(raw_value).strip()

            if name == 'RANGE_START':
                config['RANGE_START_HOUR'], config['RANGE_START_MINUTE'] = value
            elif name == 'RANGE_END':
                config['RANGE_END_HOUR'], config['RANGE_END_MINUTE'] = value
            elif name == 'TRADE_END':
                config['TRADE_END_HOUR'], config['TRADE_END_MINUTE'] = value
            else:
                config[name] = value
        return config

    def estimate_grid_runs(config):
        def count_value(name):
            value = config.get(name, [])
            return len(value) if isinstance(value, list) else 1

        be_count = 0
        skipped_be = 0
        for be_enabled in config.get('GRID_BREAKEVEN_ENABLED_OPTIONS', [BREAKEVEN_ENABLED]):
            triggers = config.get('GRID_BREAKEVEN_TRIGGER_R_OPTIONS', [BREAKEVEN_TRIGGER_R]) if be_enabled else [BREAKEVEN_TRIGGER_R]
            offsets = config.get('GRID_BREAKEVEN_OFFSET_OPTIONS', [BREAKEVEN_OFFSET]) if be_enabled else [BREAKEVEN_OFFSET]
            for trigger in triggers:
                for offset in offsets:
                    if not is_valid_breakeven_settings(be_enabled, trigger, offset):
                        skipped_be += 1
                    else:
                        be_count += 1

        trail_count = 0
        for trail_enabled in config.get('GRID_TRAILING_SL_ENABLED_OPTIONS', [TRAILING_SL_ENABLED]):
            if trail_enabled:
                trail_count += (
                    count_value('GRID_TRAILING_SL_ACTIVATE_R_OPTIONS')
                    * count_value('GRID_TRAILING_SL_DISTANCE_R_OPTIONS')
                    * count_value('GRID_TRAILING_SL_STEP_R_OPTIONS')
                    * count_value('GRID_TRAILING_SL_LOCK_BREAKEVEN_OPTIONS')
                )
            else:
                trail_count += 1

        ema_count = 0
        for ema_enabled in config.get('GRID_EMA_FILTER_OPTIONS', [EMA_FILTER_ENABLED]):
            ema_count += count_value('GRID_EMA_PERIOD_OPTIONS') if ema_enabled else 1

        total = (
            count_value('GRID_SL_MULTIPLIERS')
            * count_value('GRID_RR_RATIOS')
            * count_value('GRID_RANGE_WINDOWS')
            * len(resolve_grid_sl_placement_options())
            * len(resolve_grid_strategy_mode_options())
            * len(resolve_grid_entry_mode_options())
            * max(be_count, 0)
            * max(trail_count, 0)
            * max(ema_count, 0)
            * count_value('GRID_PENDING_EXPIRY_BARS_OPTIONS')
            * count_value('GRID_TRADE_END_OPTIONS')
        )
        return int(total), int(skipped_be)

    def validate_gui_config(config, mode):
        errors = []
        warnings = []
        range_start = pd.Timestamp(f"{int(config['RANGE_START_HOUR']):02d}:{int(config['RANGE_START_MINUTE']):02d}:00").time()
        range_end = pd.Timestamp(f"{int(config['RANGE_END_HOUR']):02d}:{int(config['RANGE_END_MINUTE']):02d}:00").time()
        trade_end = pd.Timestamp(f"{int(config['TRADE_END_HOUR']):02d}:{int(config['TRADE_END_MINUTE']):02d}:00").time()
        if range_start >= range_end:
            errors.append("Range baslangic, range bitisten once olmali.")
        if range_end >= trade_end:
            errors.append("Trade bitis, range bitisten sonra olmali.")
        if float(config.get('SL_MULTIPLIER', 0)) <= 0:
            errors.append("SL carpan > 0 olmali.")
        if float(config.get('RR_RATIO', 0)) <= 0:
            errors.append("RR oran > 0 olmali.")
        if bool(config.get('BREAKEVEN_ENABLED')) and not is_valid_breakeven_settings(
            True,
            config.get('BREAKEVEN_TRIGGER_R'),
            config.get('BREAKEVEN_OFFSET'),
        ):
            errors.append("BE offset, BE triggerdan kucuk olmali. Ornek: trigger 0.5, offset 0.0.")
        if bool(config.get('TRAILING_SL_ENABLED')) and float(config.get('TRAILING_SL_DISTANCE_R', 0)) <= 0:
            errors.append("Trail mesafe R > 0 olmali.")
        if mode == 'grid':
            total_runs, skipped_be = estimate_grid_runs(config)
            if total_runs <= 0:
                errors.append("Grid icin gecerli kombinasyon kalmadi.")
            if total_runs > 5000:
                warnings.append(f"Grid {total_runs} kombinasyon hazirliyor; bu uzun surebilir.")
            if skipped_be > 0:
                warnings.append(f"{skipped_be} mantiksiz BE kombinasyonu atlanacak.")
        return errors, warnings

    def update_summary():
        nonlocal summary_after_id
        summary_after_id = None
        try:
            config = read_config_from_gui()
            grid_runs, skipped_be = estimate_grid_runs(config)
            data_label = Path(config.get('DATA_FILE', '')).name or str(config.get('DATA_FILE', ''))
            lines = [
                f"Data: {data_label}",
                f"Tarih: {config.get('START_DATE') or 'tum veri'}",
                (
                    "Range: "
                    f"{int(config['RANGE_START_HOUR']):02d}:{int(config['RANGE_START_MINUTE']):02d}-"
                    f"{int(config['RANGE_END_HOUR']):02d}:{int(config['RANGE_END_MINUTE']):02d}, "
                    f"End {int(config['TRADE_END_HOUR']):02d}:{int(config['TRADE_END_MINUTE']):02d}"
                ),
                f"Mod: {config.get('STRATEGY_MODE')} / Entry: {config.get('ENTRY_MODE')}",
                f"SL: {config.get('SL_PLACEMENT')} x{config.get('SL_MULTIPLIER')}  RR: {config.get('RR_RATIO')}",
                f"Risk: {config.get('RISK_MODE')} ({config.get('RISK_PERCENT_PER_TRADE')}% veya {config.get('FIXED_RISK_PER_TRADE')})",
                f"BE: {'on' if config.get('BREAKEVEN_ENABLED') else 'off'}  Trail: {'on' if config.get('TRAILING_SL_ENABLED') else 'off'}",
                f"Grid: {grid_runs} kombinasyon",
            ]
            if skipped_be:
                lines.append(f"BE atlanacak: {skipped_be}")
            summary_text.set("\n".join(lines))
        except Exception as exc:
            summary_text.set(f"Ayar hatasi:\n{exc}")

    def browse_data_file():
        file_path = filedialog.askopenfilename(
            title='Data CSV sec',
            filetypes=[('CSV files', '*.csv'), ('All files', '*.*')],
            initialdir=str(Path(__file__).resolve().parent),
        )
        if file_path:
            set_field('DATA_FILE', file_path)

    def apply_defaults():
        for name, default in default_values.items():
            set_field(name, default)
        update_summary()

    def apply_clear_solo():
        set_field('SHOW_BALANCE_PLOT', True)
        set_field('SHOW_TRADE_PLOTS', True)
        set_field('WINDOW_ROBUSTNESS_ENABLED', False)
        set_field('CURVE_CHECK_ENABLED', True)
        set_field('DISTRIBUTION_ANALYSIS_ENABLED', True)
        set_field('PROP_ENABLED', True)
        set_field('GRID_MC_ENABLED', False)
        set_field('GRID_ROBUSTNESS_ENABLED', False)
        update_summary()

    def apply_fast_grid():
        set_field('SHOW_BALANCE_PLOT', False)
        set_field('SHOW_TRADE_PLOTS', False)
        set_field('WINDOW_ROBUSTNESS_ENABLED', False)
        set_field('GRID_MC_ENABLED', False)
        set_field('GRID_ROBUSTNESS_ENABLED', False)
        set_field('GRID_SL_MULTIPLIERS', '1.5, 2.0')
        set_field('GRID_RR_RATIOS', '1.0, 2.0')
        set_field('GRID_BREAKEVEN_ENABLED_OPTIONS', 'True, False')
        set_field('GRID_TRAILING_SL_ENABLED_OPTIONS', 'False, True')
        update_summary()

    def run_mode(mode):
        try:
            config = read_config_from_gui()
            config['GRID_SEARCH_ENABLED'] = mode == 'grid'
            errors, warnings = validate_gui_config(config, mode)
            if errors:
                messagebox.showerror('Ayar hatasi', "\n".join(errors))
                return
            if warnings:
                proceed = messagebox.askyesno('Kontrol et', "\n".join(warnings) + "\n\nYine de calistirilsin mi?")
                if not proceed:
                    return
            config_path = _open_run_in_new_terminal(mode, config)
            messagebox.showinfo(
                'Calistirildi',
                f"{'Grid search' if mode == 'grid' else 'Solo backtest'} yeni terminalde baslatildi.\nConfig: {config_path}",
            )
        except Exception as exc:
            messagebox.showerror('Hata', str(exc))

    for spec in fields:
        add_field(spec)

    ttk.Button(tabs['setup'], text='CSV Sec', command=browse_data_file).grid(row=1, column=1, sticky='ne', padx=(0, 12), pady=(11, 0))
    ttk.Button(preset_box, text='Varsayilanlara don', command=apply_defaults).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 6))
    ttk.Button(preset_box, text='Net solo test', command=apply_clear_solo).grid(row=1, column=0, sticky='ew', padx=(0, 4))
    ttk.Button(preset_box, text='Hizli grid', command=apply_fast_grid).grid(row=1, column=1, sticky='ew', padx=(4, 0))
    ttk.Button(run_box, text='Solo Backtest', style='Run.TButton', command=lambda: run_mode('solo')).grid(row=0, column=0, sticky='ew', padx=(0, 4))
    ttk.Button(run_box, text='Grid Search', style='Run.TButton', command=lambda: run_mode('grid')).grid(row=0, column=1, sticky='ew', padx=(4, 0))
    ttk.Label(run_box, text='Sonuclar ayri terminal penceresinde gorunur.', style='Small.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))

    update_summary()
    root.mainloop()


def main():
    warn_on_bar_time_mismatch()
    if '--discover' in sys.argv:
        # Sembolik regresyon ile strateji keşfi
        print("Strateji keşfi başlatılıyor...")
        run_discovery_and_backtest(df, generations=20, population_size=800)
    elif GRID_SEARCH_ENABLED:
        run_grid_search(df)
    else:
        run_backtest(df)


def _entrypoint():
    try:
        _mp.freeze_support()
    except Exception:
        pass

    args = set(sys.argv[1:])
    should_pause = '--pause' in args
    try:
        if '--run-solo' in args:
            globals()['GRID_SEARCH_ENABLED'] = False
            main()
        elif '--run-grid' in args:
            globals()['GRID_SEARCH_ENABLED'] = True
            main()
        else:
            main()
    except Exception:
        traceback.print_exc()
        if should_pause:
            input("\nPress Enter to close...")
        raise
    else:
        if should_pause:
            input("\nRun finished. Press Enter to close...")

# =============================================================================
# EDGE DISCOVERY (GENETIC ALGORITHM) - GÜVENİLİR VE ORİJİNAL MOTOR ENTEGRASYONLU
# =============================================================================

try:
    from deap import base, creator, tools, algorithms
    import numpy as np
    DEAP_AVAILABLE = True
except ImportError:
    DEAP_AVAILABLE = False
    print("WARNING: DEAP library not installed. Edge discovery disabled. Install with: pip install deap")

if DEAP_AVAILABLE:

    # ---------------------------------------------------------
    # 1. KULLANICI KONTROL PANELİ: EDGE ARAMA KISITLAMALARI
    # ---------------------------------------------------------
    GA_SETTINGS = {
        # --- ZAMAN KISITLAMALARI ---
        # Stratejinin test etmesini istediğin BAŞLANGIÇ saatleri (Listede olmayan saat denenmez)
        'ALLOWED_START_HOURS': [13, 14, 15],
        
        # Stratejinin test etmesini istediğin BİTİŞ saatleri
        'ALLOWED_END_HOURS': [14, 15, 16],
        
        # GÜN SONU KESİN ÇIKIŞ (Bu saatte açık işlemler market fiyatından kapatılır)
        'HARD_CLOSE_HOUR': 19,
        'HARD_CLOSE_MINUTE': 0,

        # --- DİĞER PARAMETRE UZAYLARI (Min, Max, Adım) veya [Liste] ---
        'SL_MULTIPLIER': (1.0, 4.0, 0.5),
        'RR_RATIO': (1.0, 4.0, 0.5),
        'ENTRY_MODE': ['normal', 'equilibrium', 'market'],
        'STRATEGY_MODE': ['normal', 'inverse'],
        'SL_PLACEMENT': ['distance', 'range'],
        'BREAKEVEN_ENABLED': [True, False],
        'TRAILING_SL_ENABLED': [True, False],
    }

    # Deap Tanımlamaları (FitnessMax: Amacımız Edge Score'u maksimize etmek)
    creator.create("FitnessMax", base.Fitness, weights=(1.0,))
    creator.create("Individual", list, fitness=creator.FitnessMax)
    toolbox = base.Toolbox()

    def ga_random_individual():
        """Belirlenen ayarlara göre rastgele bir strateji geni oluşturur."""
        ind = []
        for key, spec in GA_SETTINGS.items():
            if key in ['HARD_CLOSE_HOUR', 'HARD_CLOSE_MINUTE']:
                continue # Bunlar sabit, genetiğe dahil edilmez
                
            if isinstance(spec, list):
                ind.append(random.choice(spec))
            else:
                lo, hi, step = spec
                n_steps = int(round((hi - lo) / step))
                val = lo + random.randint(0, n_steps) * step
                ind.append(round(val, 2))
        return ind

    toolbox.register("individual", tools.initIterate, creator.Individual, ga_random_individual)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)

    def ga_mutate(individual, indpb=0.2):
        """Mevcut genleri mutasyona uğratarak yeni ihtimaller dener."""
        gene_keys = [k for k in GA_SETTINGS.keys() if k not in ['HARD_CLOSE_HOUR', 'HARD_CLOSE_MINUTE']]
        for i, key in enumerate(gene_keys):
            if random.random() < indpb:
                spec = GA_SETTINGS[key]
                if isinstance(spec, list):
                    individual[i] = random.choice(spec)
                else:
                    lo, hi, step = spec
                    n_steps = int(round((hi - lo) / step))
                    val = lo + random.randint(0, n_steps) * step
                    individual[i] = round(val, 2)
        return (individual,)

    toolbox.register("mutate", ga_mutate, indpb=0.2)
    toolbox.register("mate", tools.cxTwoPoint)
    toolbox.register("select", tools.selTournament, tournsize=3)

    def ga_evaluate(individual, df, data_cache):
        """
        Senin GÜVENİLİR VE ORİJİNAL run_backtest motorunu kullanarak stratejiyi test eder.
        """
        gene_keys = [k for k in GA_SETTINGS.keys() if k not in ['HARD_CLOSE_HOUR', 'HARD_CLOSE_MINUTE']]
        params = dict(zip(gene_keys, individual))

        # Mantık Filtresi: Başlangıç saati, bitiş saatinden büyük veya eşit olamaz.
        if params['ALLOWED_START_HOURS'] >= params['ALLOWED_END_HOURS']:
            return (-1000.0,) # Kötü puan vererek bu ihtimali eleriz.

        # Senin orjinal run_backtest'inin kabul ettiği overrides yapısını kullanıyoruz
        overrides = {
            'RANGE_START_HOUR': params['ALLOWED_START_HOURS'],
            'RANGE_START_MINUTE': 0,
            'RANGE_END_HOUR': params['ALLOWED_END_HOURS'],
            'RANGE_END_MINUTE': 0,
            
            # Gün sonu net kapanış saati
            'TRADE_END_HOUR': GA_SETTINGS['HARD_CLOSE_HOUR'],
            'TRADE_END_MINUTE': GA_SETTINGS['HARD_CLOSE_MINUTE'],
            
            'SL_MULTIPLIER': params['SL_MULTIPLIER'],
            'RR_RATIO': params['RR_RATIO'],
            'ENTRY_MODE': params['ENTRY_MODE'],
            'STRATEGY_MODE': params['STRATEGY_MODE'],
            'SL_PLACEMENT': params['SL_PLACEMENT'],
            'BREAKEVEN_ENABLED': params['BREAKEVEN_ENABLED'],
            'TRAILING_SL_ENABLED': params['TRAILING_SL_ENABLED'],
        }

        try:
            # Senin GÜVENİLİR motorunu çağırıyoruz
            trades = run_backtest(
                data=df, 
                silent=True, 
                save_trades=False, 
                show_plots=False, 
                run_distribution=False, 
                run_curve_check=False, 
                run_window_robustness=False, 
                data_cache=data_cache, 
                overrides=overrides
            )
            
            if trades is None or trades.empty:
                return (-1000.0,)

            stats = compute_trade_stats(trades)
            
            # Senin yazdığın Orijinal Edge Puanlama sistemini (compute_edge_score_from_row) 
            # beslemek için sahte bir satır (row) oluşturuyoruz. 
            row = {
                'profit_factor': stats['profit_factor'],
                'total_trades': stats['total_trades'],
                'max_drawdown_pct': stats['max_drawdown_pct'],
                'total_pnl': stats['total_pnl'],
                'sharpe': stats['sharpe'],
            }
            
            # Senin formülünle hesaplanmış net Edge Score
            edge_score = compute_edge_score_from_row(row)
            
            if stats['total_trades'] < 30: # Yeterli işlem sayısına ulaşamayanları cezalandır
                edge_score *= 0.5

            return (edge_score,)
            
        except Exception:
            return (-1000.0,)

    def run_discovery_and_backtest(df, generations=15, population_size=50):
        if not DEAP_AVAILABLE:
            print("DEAP not installed. Cannot run Edge Discovery.")
            return

        # Veri önbelleğini (cache) bir kere oluştur, GA döngüsünde zaman kazan
        print(f"\n--- Veri önbelleğe alınıyor (Veri satır sayısı: {len(df)}) ---")
        data_cache = get_data_cache(df, ema_filter_enabled=EMA_FILTER_ENABLED, ema_period=EMA_PERIOD)

        toolbox.register("evaluate", ga_evaluate, df=df, data_cache=data_cache)
        
        pop = toolbox.population(n=population_size)
        hof = tools.HallOfFame(1)
        stats = tools.Statistics(lambda ind: ind.fitness.values)
        stats.register("avg", np.mean)
        stats.register("max", np.max)

        print(f"\n=== EDGE DISCOVERY (YAPAY ZEKA KEŞFİ) BAŞLIYOR ===")
        print(f"Jenerasyon: {generations}, Populasyon: {population_size}")
        print(f"Hard Close: {GA_SETTINGS['HARD_CLOSE_HOUR']:02d}:{GA_SETTINGS['HARD_CLOSE_MINUTE']:02d}")
        
        # Paralel işlem havuzu ile hızı katla
        pool = _mp.Pool(processes=max(1, _mp.cpu_count() - 1))
        toolbox.register("map", pool.map)
        
        pop, log = algorithms.eaSimple(pop, toolbox, cxpb=0.6, mutpb=0.3, ngen=generations,
                                       stats=stats, halloffame=hof, verbose=True)
        pool.close()

        best = hof[0]
        gene_keys = [k for k in GA_SETTINGS.keys() if k not in ['HARD_CLOSE_HOUR', 'HARD_CLOSE_MINUTE']]
        best_params = dict(zip(gene_keys, best))
        best_fitness = best.fitness.values[0]

        print("\n" + "="*60)
        print(f"💎 EN İYİ STRATEJİ (EDGE) BULUNDU! (Skor: {best_fitness:.2f})")
        print("="*60)
        print(f"► Range Window : {best_params['ALLOWED_START_HOURS']:02d}:00 - {best_params['ALLOWED_END_HOURS']:02d}:00")
        print(f"► Trade End    : {GA_SETTINGS['HARD_CLOSE_HOUR']:02d}:{GA_SETTINGS['HARD_CLOSE_MINUTE']:02d} (Market Close)")
        print(f"► Strategy Mode: {best_params['STRATEGY_MODE']}")
        print(f"► Entry Mode   : {best_params['ENTRY_MODE']}")
        print(f"► SL Placement : {best_params['SL_PLACEMENT']}")
        print(f"► SL Multiplier: {best_params['SL_MULTIPLIER']}")
        print(f"► RR Ratio     : {best_params['RR_RATIO']}")
        print(f"► Break-Even   : {'Açık' if best_params['BREAKEVEN_ENABLED'] else 'Kapalı'}")
        print(f"► Trailing SL  : {'Açık' if best_params['TRAILING_SL_ENABLED'] else 'Kapalı'}")
        print("="*60)
        
        print("\nBulunan en iyi strateji ile son bir tam detaylı Backtest çalıştırılıyor...\n")
        
        # Bulunan en iyi ayarları global değişkenlere uygulayıp senin tam teşekküllü testini çalıştırıyoruz
        overrides = {
            'RANGE_START_HOUR': best_params['ALLOWED_START_HOURS'],
            'RANGE_START_MINUTE': 0,
            'RANGE_END_HOUR': best_params['ALLOWED_END_HOURS'],
            'RANGE_END_MINUTE': 0,
            'TRADE_END_HOUR': GA_SETTINGS['HARD_CLOSE_HOUR'],
            'TRADE_END_MINUTE': GA_SETTINGS['HARD_CLOSE_MINUTE'],
            'SL_MULTIPLIER': best_params['SL_MULTIPLIER'],
            'RR_RATIO': best_params['RR_RATIO'],
            'ENTRY_MODE': best_params['ENTRY_MODE'],
            'STRATEGY_MODE': best_params['STRATEGY_MODE'],
            'SL_PLACEMENT': best_params['SL_PLACEMENT'],
            'BREAKEVEN_ENABLED': best_params['BREAKEVEN_ENABLED'],
            'TRAILING_SL_ENABLED': best_params['TRAILING_SL_ENABLED'],
        }
        
        run_backtest(df, overrides=overrides)

else:
    def run_discovery_and_backtest(*args, **kwargs):
        print("Genetic algorithm not available. Install DEAP: pip install deap")

if __name__ == '__main__':
    _entrypoint()
