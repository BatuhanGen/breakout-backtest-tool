# Intraday Breakout Backtester

A Python project I built to research and backtest intraday breakout strategies using historical market data.

The project started with a fairly simple idea: build a backtester for a range breakout strategy and see whether it actually had an edge.

As I kept testing it, I started finding problems with simple backtesting. Things like the exact range time, intrabar execution, slippage, risk sizing, parameter optimization and overfitting could change the results quite a lot.

So instead of keeping it as a small backtest script, I gradually turned it into a research tool where I could test those problems directly.

The code is still evolving. I am building this mainly to learn more about Python, quantitative finance, statistics and systematic trading.

## What it can do

The project currently includes:

* Intraday OHLC backtesting
* Long and short strategies
* Normal and inverse breakout logic
* Multiple entry methods
* Range-based and breakout-candle-based stop loss
* Fixed or percentage-based risk
* Break-even stops
* Trailing stops
* Spread, slippage and commission modelling
* Daily trade limits
* Prop-firm style drawdown rules
* In-sample / out-of-sample analysis
* Rolling OOS analysis
* Parameter grid search
* Parallel grid search
* Range-window robustness testing
* Monte Carlo analysis
* Trade distribution analysis
* Optional news filtering
* Interactive trade charts
* Tkinter configuration GUI
* Experimental genetic-algorithm based parameter search

## The strategy

Most of my testing so far has been around a time-based range breakout.

A range is created between two configurable times. After the range is finished, the backtester waits for price to move outside of it.

For example:

```text
Range
14:30 → 15:00

Break above Range High → LONG
Break below Range Low  → SHORT
```

There is also an inverse mode:

```text
Break above Range High → SHORT
Break below Range Low  → LONG
```

The range times, trading session and strategy direction are configurable.

## Entry modes

I currently use three entry modes.

### Market

Enter immediately after the breakout using a market order.

### Normal

Use the broken range boundary as the entry level.

### Equilibrium

After the breakout, place the entry around the middle of the range.

I added these because I wanted to compare different ways of entering the exact same idea instead of assuming that one entry method was automatically better.

## Stop loss and take profit

The stop loss can be based on either:

* The size of the range
* The size of the breakout candle

The stop distance is then multiplied by `SL_MULTIPLIER`.

Take profit is calculated from the stop distance and `RR_RATIO`.

For example:

```python
SL_PLACEMENT = "range"
SL_MULTIPLIER = 0.5
RR_RATIO = 3.0
```

This makes the stop equal to half of the range size and the target equal to 3R.

## Managing trades

The backtester supports both break-even and trailing-stop logic.

### Break-even

The stop can be moved to the entry price after the trade reaches a certain amount of profit.

The trigger can use either:

* Wick
* Candle close

There is also a setting for whether the stop can be changed inside the same candle that triggered it.

I added this because OHLC data does not tell us the exact sequence of prices inside a candle.

### Trailing stop

The trailing stop can be activated after a configurable amount of profit and moved behind price by a configurable R distance.

It can also be configured to protect the break-even level once it becomes active.

## Execution

This is one of the areas where I tried to make the backtester more conservative.

With normal OHLC data, there are situations where the same candle touches both the take profit and stop loss. We know both levels were reached, but we do not know which one happened first.

The backtester therefore supports different assumptions:

```text
worst_case
best_case
tp_first
sl_first
```

I can also model:

* Spread / execution cost in price points
* Fixed cost per trade
* Pending-order behaviour
* Pending-order expiry
* Same-bar fills
* Opposite breakout cancellation
* End-of-day position closing

The idea is to make execution assumptions explicit instead of letting them silently make the backtest look better.

## Risk management

Position size can be calculated in two basic ways:

```text
fixed
percent of current equity
```

There is also an optional dynamic risk layer with:

* Martingale
* Anti-martingale
* Anti-martingale ladder
* Minimum risk
* Maximum risk
* Prop-firm safety limits

I mainly use these settings for experimentation and risk analysis rather than assuming that more aggressive sizing automatically improves a strategy.

## Prop-firm testing

I added a separate layer for testing strategies under prop-firm style account rules.

It can model:

* Profit targets
* Maximum drawdown
* Daily drawdown
* Static drawdown
* Trailing drawdown
* Daily drawdown from starting equity
* Daily drawdown from peak equity

There is also Monte Carlo analysis for the prop-firm side of the project.

This lets me look at questions like:

> A strategy may be profitable overall, but how often would its trade sequence actually hit an account's drawdown limit?

That is a more useful question to me than looking at total PnL alone.

## Testing for overfitting

One of the biggest reasons I kept expanding this project was overfitting.

It is very easy to test a large number of settings, find one combination that looks amazing, and then assume that the strategy is good.

I wanted the backtester to make that harder.

### In-sample vs out-of-sample

The project can split trades into in-sample and out-of-sample sections and compare things like:

* Profit Factor
* Win Rate
* Sharpe
* PnL

The idea is simple: if performance only exists in the part of the data that was used for testing or parameter selection, that is a warning sign.

### Rolling OOS

There is also a rolling out-of-sample report.

This is worth clarifying because I do not want to overstate what it does: the current implementation evaluates rolling OOS windows, but it is **not** a full walk-forward system that re-optimizes the strategy before every new test window.

I kept the distinction explicit because calling every rolling test “walk-forward optimization” would be misleading.

## Range-window robustness

I also wanted to see how dependent a strategy was on one exact timestamp.

Suppose a strategy uses:

```text
14:30 → 15:00
```

I can shift that window by a few minutes and run the same test again.

For example:

```text
14:28 → 14:58
14:29 → 14:59
14:30 → 15:00
14:31 → 15:01
14:32 → 15:02
```

The purpose is not to find another perfect parameter.

It is to see whether the original result survives small changes.

The project calculates several values from these tests, including:

* Profit Factor pass ratio
* PnL pass ratio
* Median shifted Profit Factor
* Profit Factor variation
* Stability factor
* Robustness score

A strategy that only works at one exact minute is much more interesting to investigate than simply calling it “optimized.”

## Grid search

I can automatically test combinations of parameters instead of changing them manually.

The grid can vary things such as:

```text
Range window
SL multiplier
RR ratio
Entry mode
Strategy direction
SL placement
Break-even settings
Trailing-stop settings
EMA filter
Trade end time
Pending-order expiry
Risk settings
Execution assumptions
```

Results can be ranked using several metrics, including:

```text
Edge Score
Robustness Score
OOS Profit Factor
Walk-forward median PF
Profit Factor
Sharpe
Total PnL
```

The grid search can also use multiple CPU processes so larger parameter searches do not have to run entirely on one core.

## Edge Discovery

There is an experimental genetic-algorithm section using DEAP.

The idea is to let the program search through a parameter space rather than manually testing every possible combination.

Some of the parameters it can explore include:

```text
Range start/end time
SL multiplier
RR ratio
Entry mode
Strategy direction
SL placement
Break-even
Trailing stop
```

An important detail here is that it still uses the main backtesting engine to evaluate the candidates.

I added this mainly as an experiment. It is not something I consider finished yet.

## Statistics

After each backtest, the project calculates statistics such as:

* Total trades
* Win rate
* Profit Factor
* Sharpe Ratio
* Total PnL
* Average PnL
* Average win
* Average loss
* Payoff ratio
* Expectancy
* Average R
* Maximum drawdown
* Maximum drawdown %
* Maximum consecutive wins
* Maximum consecutive losses

The equity curve is calculated from the sequence of simulated trades.

I also use the individual trade results for the out-of-sample, robustness and Monte Carlo checks.

## Data

The project is mainly designed for intraday CSV data.

It can automatically detect common separators:

```text
,
;
\t
|
```

It supports several common OHLC formats, including:

```text
DateTime, Open, High, Low, Close
```

```text
time, open, high, low, close
```

and MetaTrader-style data:

```text
<DATE>, <TIME>, <OPEN>, <HIGH>, <LOW>, <CLOSE>
```

The timestamp convention can also be configured so the backtester knows whether a timestamp represents the beginning or end of a candle.

## Charts

I wanted to be able to inspect trades visually instead of trusting terminal statistics alone.

The project can generate candlestick charts for individual trades and mark things such as the range, entry and exit.

There are currently two chart approaches:

* Matplotlib
* Lightweight Charts

This has been particularly useful when debugging the backtester. If a trade looks strange in the statistics, I can look at the actual price movement and check whether the result makes sense.

## GUI

I also built a small Tkinter interface around the configuration.

It can be used to:

* Select the data file
* Change strategy settings
* Change risk settings
* Configure grid-search parameters
* Run a normal backtest
* Run a grid search

The GUI passes the selected configuration to the main script instead of containing a separate version of the strategy logic.

That way, I can keep using the same backtesting engine whether I start a run manually or through the interface.

## Running

The main script is:

```text
main_strategy.py
```

The simplest way to run it is:

```bash
python main_strategy.py
```

Most settings are currently defined near the top of the file.

Some of the main ones are:

```python
DATA_FILE

RANGE_START_HOUR
RANGE_START_MINUTE
RANGE_END_HOUR
RANGE_END_MINUTE
TRADE_END_HOUR
TRADE_END_MINUTE

STRATEGY_MODE
ENTRY_MODE

SL_PLACEMENT
SL_MULTIPLIER
RR_RATIO

RISK_MODE
RISK_PERCENT_PER_TRADE

GRID_SEARCH_ENABLED
```

The script can also be started in different modes internally, including solo backtesting, grid search and the experimental discovery mode.

## Project structure

The repository is currently fairly simple:

```text
.
├── main_strategy.py
├── config.json
└── ohlc_data/
    └── US100data/
        └── NDX100/
            └── NDX100_M1.csv
```

The structure will probably change over time as I separate different parts of the project into their own modules.

## How I built it

I used AI coding tools during the development of this project.

They helped with things like generating and changing code, explaining errors, and speeding up parts of the implementation.

But the project itself was not something I generated once and left running.

The direction of the project, the original idea, the features I wanted to investigate, the experiments I decided to run, and the decisions about how the backtester should behave came from me.

A lot of the development was iterative: I would test something, find a problem, change the implementation, run it again, and keep going.

That process is a big part of why the project ended up becoming much larger than the original idea.

## Why I built this

I became interested in algorithmic trading and started experimenting with very simple backtests.

At first, I mainly cared about whether a strategy made money.

Then I started asking better questions.

What happens if I move the trading window?

What happens if I include realistic execution costs?

What if the strategy only works because of one specific parameter?

What if the in-sample results are strong but the out-of-sample results are not?

What if the strategy is profitable but still violates a drawdown rule?

Those questions are what pushed this project from a basic backtester into a much bigger research project.

I am still learning, so I do not consider this finished software.

For me, the value of the project is not just the final code. It is also everything I learned while trying to make the backtests harder to fool.

## What I want to improve

There are still several things I want to work on.

Some of them are:

* Splitting the large script into cleaner modules
* Improving execution modelling
* Adding more validation methods
* Improving the statistical analysis
* Improving the GUI
* Making the research workflow easier to reproduce
* Testing more markets and datasets
* Improving the experimental edge-discovery section

## Disclaimer

This project is for educational and research purposes.

Backtest results are not proof that a strategy will work in live trading.

Historical data, execution, liquidity, spread, slippage and market behaviour can all differ from the assumptions used by a backtest.
