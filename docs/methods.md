# Methods note

This note documents every transformation between a raw official release and a number on
the dashboard. Code references are to the `cetracker` package.

## 1. Data architecture and traceability

**Store.** One long table (`data/store/observations.parquet`; query through DuckDB with
`store.duckdb_view()`). Each row is one value of one series for one period *as known at
one vintage*:

| column | meaning |
|---|---|
| `series_id` | registry key (`cetracker/registry.py`); derived ids are `<base>~<transform>` |
| `period` | month-end or quarter-end date |
| `value` | float |
| `vintage_date` | date the value became public (release date where parseable, else the first date the tracker saw that exact response) |
| `source_tier` | `primary` (official source), `fallback` (secondary source, always flagged), `derived` (computed), `manual` (transcribed, must cite URL) |
| `source`, `source_url` | where it came from |
| `raw_file` | the archived response the value was parsed from |
| `retrieved_at` | UTC time of first retrieval of that response |
| `lineage` | for fetched rows: resolved source code, e.g. `nbs:hgyd:A070101:社会消费品零售总额_当期值`; for derived rows: `transform(inputs)` |
| `flags` | e.g. `janfeb_combined`, `janfeb_split`, `from_ytd_diff` |

**Rules enforced in code** (`store.validate`): fetched rows must reference a raw file;
derived rows must carry lineage; manual rows must cite a URL; NaNs are never stored.

**Vintages.** `store.append` writes a row only when the (series, period) is new or its value
differs from the latest stored vintage. Unchanged history is never re-stamped, so the store's
vintages are first-publication and revision dates. `store.as_of(date)` rebuilds what was
known on any date.

**Raw archive.** Raw files are content-addressed: `data/raw/<source>/<request-hash>/<content-hash>.<ext>`,
each with a `.meta.json` holding the URL, parameters and first retrieval time. An unchanged
response creates no new file.

**Indicator resolution.** NBS portal codes are discovered from the portal's own indicator
tree (`m=getTree`) and matched by Chinese name. A pattern that matches nothing fails loudly
in `source_status.json`. The resolved code and name are written into lineage, so a silent
renumbering cannot substitute a different indicator.

**Two raw routes.** Some figures exist only in release *text*: PBoC loan splits by sector and
tenor, MOF local-bond issuance by type, NDRC approval counts, NBS tier housing commentary and
infrastructure FAI growth. The parsers in `fetch/releases.py` split a text into period blocks
(single month / year-to-date / month-end stock) and extract only explicitly stated figures;
nothing is inferred. Where a release gives only year-to-date figures, single months are YTD
differences, flagged `from_ytd_diff`.

## 2. China-specific data treatment

### 2.1 Year-to-date data and the Jan–Feb rule (`transform/ytd.py`)
- Single month m ≥ 3: `YTD_m − YTD_{m−1}`. Feb holds the Jan–Feb total (flag `janfeb_combined`).
  A month following a missing month is left empty, never absorbed.
- For monthly analytics the Jan–Feb total is split evenly into Jan and Feb (flag `janfeb_split`).
  For growth rates, January is set equal to the Jan–Feb figure (`fill_janfeb`). Any split is
  unobservable. The even split keeps the Jan+Feb sum and the y/y exact, and the holiday effect
  is then handled in seasonal adjustment (2.3).

### 2.2 Comparable-basis growth and the y/y gap log
NBS growth is computed on a comparable sample: the above-designated-size firm sample is
redrawn each year and prior-year figures are revised. Growth computed from published levels
therefore differs from published growth. The tracker:
1. Reconstructs the implied comparable base from each release: `B_m = YTD_m / (1 + g_m)`.
2. Computes official-consistent single-month growth: `(YTD_m − YTD_{m−1}) / (B_m − B_{m−1}) − 1` (`~cyoy`).
   This reproduces NBS's published single-month growth where NBS publishes it (retail
   sales, a validation check). Where NBS does not publish it (FAI, property), it gives a
   single-month figure consistent with the official one.
3. Logs the gap between y/y computed naively from levels and published y/y (`~nyoy`,
   validation "Published y/y reproduction"). The gap is information about NBS's base
   adjustments, not an error.

### 2.3 Seasonal adjustment (`transform/seasonal.py`)
- **Primary:** X-13ARIMA-SEATS (Census binary via the `x13binary` wheel), X-11 decomposition,
  automatic ARIMA model selection. Three user-defined Spring Festival regressors: pre-holiday
  [−20, −1] days, holiday week [0, +6], post-holiday restart [+7, +20]. Each regressor is the
  share of the window falling in the month, centred on its calendar-month mean over 2005–2030
  (genhol convention: only the year-to-year shift of the holiday is removed).
- **Jan–Feb series:** with Jan+Feb combined, the pre- and on-holiday windows always fall inside
  Jan+Feb. Those regressors are therefore constant and dropped, and only the post-window leak
  into March is modelled.
- **Cross-check:** STL (robust) on logs after OLS removal of the same holiday effect. Both are
  shown side by side.
- NBS publishes its own SA m/m for some series; where it is ingested it is shown next to the
  tracker SA. **Industrial production has no tracker SA**: NBS publishes no IP levels, and
  rebuilding levels from y/y would need an unknown base-year seasonal pattern.
- Only one-sided transforms feed real-time signals (trailing 3-month mean, 3m/3m SAAR on the
  SA level). No centred filters or HP filter.

### 2.4 Real growth (`transform/deflate.py`)
`real = (1 + nominal)/(1 + deflator) − 1`.
- **Retail:** deflated by CPI consumer goods.
- **FAI:** deflated by PPI as a proxy, flagged `deflator_proxy:ppi`. The NBS FAI price index
  was discontinued after 2019 (to be verified against NBS on first fetch).
- **YTD growth:** deflated with the YTD average of the deflator.

### 2.5 Structural-break register (`config/breaks.yaml`)
Breaks are annotated on every chart whose series id matches the entry's patterns. The
lead/lag analysis runs Chow tests at every register date inside its sample. Entries:
- TSF redefinitions, 2018–2019.
- M2 definition changes, 2011 and 2018.
- M1 redefinition, January 2025 (affects the M1–M2 gap).
- FAI data-quality correction, 2018.
- COVID base effects, 2020–22. Growth vs 2019 (annualised) is shown for 2021–23, and analytics
  run with and without 2020–21.
- Annual above-size sample redraw.
- 2023 economic-census GDP revision.
- Local-government debt-swap programmes: 2015–18, 2023 special refinancing, 2024–26.
- 2026 CPI/PPI rebasing.
- Jan–Feb combined releases.

Entries marked `status: unverified` have their dates checked against the primary source on
first fetch.

### 2.6 Government bonds (`transform/fiscal.py`)
- **Categories:**
  - Central: regular CGB, ultra-long special CGB, bank-recap special CGB.
  - Local: new general, new special, regular refinancing, debt-swap refinancing.
- **New money** = new general + new special (+ central new issuance). Debt-swap refinancing
  funds no new projects. Up to RMB0.8trn a year of *new* special bonds may also be used for
  swaps (2024–28); this cannot be separated at aggregate level and is flagged.
- **Pace:** cumulative issuance as a percentage of the NPC quota (`config/quotas.yaml`),
  compared with the average of the prior 3 years at the same month. Quotas transcribed from
  secondary sources are marked unverified in the app.
- **Net financing** = gross issuance − principal redemptions.
- **Fiscal impulse:** Δ12m of the 12-month issuance sum, as a % of GDP (same definition as the
  credit impulse).

### 2.7 Credit (`transform/credit.py`)
- **Credit impulse** = (F12_t − F12_{t−12}) / GDP4Q_t × 100, where F12 is the trailing 12-month
  flow. GDP4Q is trailing 4-quarter nominal GDP, using only quarters published by month t
  (quarter available one month after quarter end).
- Computed for total TSF, TSF ex-government bonds (private credit demand) and RMB loans.
- **Loans by sector and tenor:** PBoC release text gives a year-to-date breakdown, so single
  months are YTD differences.
- **M1–M2 gap** = M1 y/y − M2 y/y, with the January 2025 M1 break annotated.

### 2.8 Housing prices by tier (`transform/housing.py`)
- **Tiers:** NBS 70-city tiers (4 + 31 + 35, `config/tiers.yaml`).
- **Aggregation:** the tier m/m (y/y) is the simple mean over the tier's cities of
  (index − 100), and is reported only when every city in the tier reports.
- **Level:** a chained index, 2015 = 100.
- **Breadth:** the number of cities with m/m declines.
- **Validation:** tier figures are checked against those quoted in the NBS release text,
  with a tolerance of 0.1pp, the rounding of the published figures.

## 3. Analytics

### 3.1 Lead/lag tests (`analytics/lags.py`)
For each pair (high-frequency x, official y):
1. **Alignment:** common observations restricted to the longest run of consecutive months, so
   that positional shifts never pair the wrong months.
2. **Stationarity:** ADF and KPSS; a series failing either test is first-differenced.
3. **Prewhitening:** fit AR(p) to x (AIC, p ≤ 12), filter both series with it, then
   cross-correlate the residuals. The 95% band is ±1.96/√n.
4. **ADL:** fitted with BIC lag selection and Newey–West HAC errors. Reports Σ x-coefficients
   with its p-value and the long-run multiplier.
5. **Granger F-test** at the ADL lag order.
6. **Rolling 36-month prewhitened correlation**, to check stability.
7. **Breaks:**
   - Chow tests at register dates.
   - Quandt–Andrews sup-F for one unknown break, 15% trimming, with a fixed-regressor
     bootstrap p-value (Hansen 2000); no tabulated critical values needed.
   - This is a single-break test, not Bai–Perron multiple-break estimation.
8. **Placebos:**
   - (a) A circular block bootstrap of x (block 12) gives the null distribution of max |CCF|
     over leads.
   - (b) The reverse direction (y leading x) must not dominate.

**Publication rule:** "x leads y by k" is shown only if all four criteria hold. These are the
prewhitened CCF significance, placebo p < 0.05, Granger p < 0.05, and no reverse dominance.
Otherwise the app says "no robust lead" and still shows the evidence.

**Inference uses unsmoothed series.** In testing, running these tests on a 3-month moving
average shifted the estimated lead (a planted 3-month lead was estimated at 6 months), because
prewhitening an MA-smoothed series is imperfect. Smoothed series are display-only.

### 3.2 Nowcasts (`analytics/nowcast.py`)
- **Target:** FAI single-month growth (comparable basis). Predictors are tested one at a time
  against an AR(1) benchmark: new local bonds, special bonds, NDRC approvals, credit impulse
  ex-government, and bonds + credit.
- **GDP bridge:** quarterly averages of the first month of the quarter (IP, real retail, real
  FAI, credit impulse).
- **Ragged edge:** each predictor is lagged by its publication delay. Bonds: 0 months. PBoC
  credit, NDRC and NBS activity: 1 month.
- **Evaluation:** expanding window, scored by RMSE, relative RMSE and the Diebold–Mariano test
  with the Harvey–Leybourne–Newbold correction. Estimation excludes 2020–21; errors are
  reported with and without it.
- **Pseudo real-time:** estimation uses the latest vintage with the ragged edge replicated.
  True real-time evaluation becomes possible as the vintage store accumulates. Every live
  nowcast is appended to `data/nowcast_log.csv` before the release and scored after it.
  Only the first pre-release nowcast for a period is scored.
- **Physical proxies** (cement, steel, power) are published the same day as FAI, so they carry
  no real-time lead and are used for validation only, not as nowcast inputs.

### 3.3 Project classification (`analytics/classify.py`)
- **Method:** keyword dictionary (`config/six_networks.yaml`), first match in priority order.
- **Categories:**
  - Six networks: water, power/energy, transport, computing, urban pipes, logistics.
  - Other: social, industrial park, land reserve.
- **Unclassified:** records that match no category are kept as "unclassified".
- **Audit:** a random sample is labelled by an independent labeller who never sees the rule
  output; precision and recall by category are published.

## 4. Release calendar assumptions
- **NBS:** monthly activity around the 15th–17th of the following month, at 10:00 Beijing time.
  Quarterly GDP comes with the March, June, September and December activity data.
- **PBoC:** credit data around the 10th–15th.
- **MOF:** the local-bond report, mid-to-late month.
- **NDRC:** press conferences, irregular, roughly monthly.
- **Refresh schedule:** the scheduled refresh runs at 10:20 and 18:20 Beijing time.
