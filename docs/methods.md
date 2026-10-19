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

**Vintages.**
- `store.append_fetched` first drops rows previously parsed from any raw file re-parsed in the
  same run, so a parser fix replaces its own earlier output (wrong values, periods or vintages).
- A changed source page has a new content hash, so genuine revisions accumulate as new vintages.
- Rows whose vintage precedes their period end are rejected (they signal a wrong period or vintage).
- `store.as_of(date)` rebuilds what was known on any date.

Vintage sources:
- **NBS:** the release URL date. Pages re-dated by NBS's 2023 site migration get period end +
  17 days, flagged `vintage_estimated_archive`.
- **PBoC releases:** the page's publication time.
- **PBoC statistics tables:** no publication date, so the retrieval date, flagged
  `vintage_from_retrieval`.
- **Derived rows:** each period takes the latest vintage among its inputs' values as of that
  period, not the newest release for the whole history.

The store was reset once (19 Oct 2026) after the independent review found parser defects; it is
now a deterministic function of the archived raw responses.

**Raw archive.** Raw files are content-addressed: `data/raw/<source>/<request-hash>/<content-hash>.<ext>`,
each with a `.meta.json` holding the URL, parameters and first retrieval time. An unchanged
response creates no new file.

**Source routes actually used** (each established by probing the live sites from a GitHub runner;
`tools/probe.py`):

| Data | Route | History |
|---|---|---|
| NBS activity, prices, property, GDP, 70-city | Monthly release pages `www.stats.gov.cn/sj/zxfb/` (text + embedded tables), parsed by release type | Nov 2021 onward (oldest page in the release archive) |
| NBS 70-city tier figures (validation target) | Urban-department interpretation releases `www.stats.gov.cn/sj/sjjd/` | Same |
| TSF flow/stock, money supply, loans by sector & tenor | PBoC statistics tables `pbc.gov.cn/diaochatongjisi/116219/116319/` (HTML version of each table) | 2012 onward |
| PBoC release text (loans, TSF, M1/M2 y/y) | PBoC news list (`11040-N.html` pagination) | Reconciliation source; M1/M2 published y/y |
| Local government bonds | MOF monthly release text `yss.mof.gov.cn/zhuantilanmu/dfzgl/sjtj/` | As far as the list pages go |
| NDRC approvals | Press-conference transcript attachments (.docx, "发布会实录") | Statements are irregular |

The NBS data portal (`data.stats.gov.cn`) was the planned primary route. It returns HTTP 403 to cloud
runners even on its landing page, so it is not used. The release pages publish the same official
figures; the limitation is that their archive starts in November 2021.

**Indicator resolution (portal code, retained):** NBS portal codes would be discovered from the
portal's own indicator tree and matched by Chinese name. Release parsers likewise extract only
explicitly stated figures, and record the parser name in lineage.

**Two raw routes.** Some figures exist only in release *text*: PBoC loan splits by sector and
tenor, MOF local-bond issuance by type, NDRC approval counts, NBS tier housing commentary and
infrastructure FAI growth. The parsers in `fetch/releases.py` split a text into period blocks
(single month / year-to-date / month-end stock) and extract only explicitly stated figures;
nothing is inferred. Where a release gives only year-to-date figures, single months are YTD
differences, flagged `from_ytd_diff`. Such a difference spans two releases, so it mixes two vintages
and two roundings. Period phrases handled: single months, "1-8月", "前8个月"/"前两个月", quarter
phrases ("一季度", "上半年", "前三季度"), and the bare year in December annual reports
("2025年社会融资规模增量累计为…", read as January–December).

**Replay.** `python -m cetracker.build --replay` re-parses the whole store from `data/raw` with no
network: each request is served from the most recently first-retrieved archived response for its
request hash. Every number in the store can therefore be regenerated from archived bytes, and a
parser fix can be tested against all history before it is pushed.

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
**Input series.** Seasonal adjustment never runs on raw NBS levels: those jump each January when
the above-size sample is redrawn. It runs on an implied comparable-basis level
(`~clevel`, `ytd.chain_comparable_level`): the first 12 observed single-month levels, chained
forward with official-consistent y/y, `L_t = L_{t-12}(1+g_t)`. This applies to FAI, property and
retail (retail is chained from published single-month levels and y/y).

**Revisions.** X-11 seasonal factors are two-sided: adding data revises past SA values and the
3m/3m SAAR built on them. These series describe history and current momentum, but they are
not real-time-safe signals. Only the trailing means and the y/y-based series are one-sided.

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
- COVID, 2020–22, through the end of zero-COVID in December 2022, including the April–May 2022
  Shanghai lockdown. Lead/lag verdicts must hold on the sample without 2020–22. The nowcast
  excludes 2020–21 from estimation; excluding 2022 as well would leave too short a training sample.
- Annual above-size sample redraw.
- 2023 economic-census GDP revision.
- Local-government debt-swap programmes: 2015–18, 2023 special refinancing, 2024–26.
- 2026 CPI/PPI rebasing.
- Jan–Feb combined releases.

Entries marked `status: unverified` have their dates checked against the primary source on
first fetch.

### 2.6 Government bonds (`transform/fiscal.py`)
- **Debt-swap issuance proxy:** MOF's monthly text never states the hidden-debt swap amount.
  Since 2024-11 the swap programme is issued as refinancing special bonds, so `lgb.refi_swap_proxy`
  = refinancing special bonds from 2024-11, labelled as a proxy everywhere it appears.
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

### 2.7 Credit (`transform/credit.py`, `fetch/pboc_tables.py`)
- **Loans by sector and tenor** are monthly changes in the stocks of the PBoC "金融机构人民币信贷收支表".
  Household loans split into short and medium/long. Corporate loans split into short, medium/long,
  bills, leases and advances. Stock identities hold to 0.01 亿元. Flows from stock changes can differ
  from PBoC-reported new loans (write-offs, reclassification); the gap is a reconciliation check.
  At a change of statistical scope the stock difference is not a flow. In January 2023
  (`PBOC_SCOPE_2023`), the 2023 table's stocks include consumer-finance and other newly covered
  institutions, which the 2022 table's do not. The household stock difference is RMB 1,091bn against
  257bn of published new loans. The Jan-2023 flow is therefore the release-text figure, flagged
  `break:PBOC_SCOPE_2023,from_release_text`.
- **TSF:** from the PBoC flow table. Its published components do not sum to the total: the residual
  is PBoC's unpublished "other" category, logged as WARN. Tables for years before the 2018–19
  redefinitions have no government-bond / ABS / write-off columns. There TSF ex-government equals
  the total, flagged `govt_bonds_not_in_definition`.
- **Credit impulse** = (F12_t − F12_{t−12}) / GDP4Q_t × 100, where F12 is the trailing 12-month
  flow. GDP4Q is trailing 4-quarter nominal GDP, using only quarters published by month t
  (quarter available one month after quarter end).
- Computed for total TSF, TSF ex-government bonds (private credit demand) and RMB loans.
- **Loans by sector and tenor:** PBoC release text gives a year-to-date breakdown, so single
  months are YTD differences.
- **M1–M2 gap** = M1 y/y − M2 y/y, with the January 2025 M1 break annotated. It uses published y/y
  from the PBoC release where the release states it (sign read explicitly: M1 fell y/y from April to
  December 2024). Elsewhere it uses y/y from the money-supply table levels, flagged `from_levels`;
  2025 is computed on PBoC's restated 2024 M1 base. This takes the series back to 2013. The
  current year's table has a 12-month header but rows only up to the latest month, and is parsed
  as such.

### 2.8 Housing prices by tier (`transform/housing.py`)
- **Tiers:** NBS 70-city tiers (4 + 31 + 35, `config/tiers.yaml`).
- **Aggregation:** the tier m/m (y/y) is the simple mean over the tier's cities of
  (index − 100), and is reported only when every city in the tier reports.
- **Level:** a chained index, 2022 average = 100. Months without an m/m are gaps.
- **Breadth:** the number of cities with m/m declines.
- **Validation:** tier figures are checked against those quoted in the NBS release text,
  with a tolerance of 0.1pp, the rounding of the published figures.

## 3. Analytics

### 3.1 Lead/lag tests (`analytics/lags.py`)
For each pair (high-frequency x, official y):
1. **Alignment:** both series sit on a full monthly index; missing months, such as January for
   NBS Jan–Feb releases, stay missing. No month is filled. Lags and differences are time-based,
   and correlations use pairwise-complete months, so nothing is paired with the wrong month and no
   two-month change is treated as one month. The test runs only with ≥ 36 jointly observed months.
2. **Stationarity:** ADF and KPSS; a series failing either test is first-differenced.
3. **Prewhitening:** fit AR(p) to x (AIC, p ≤ 12), filter both series with it, then
   cross-correlate the residuals. The 95% band is ±1.96/√n.
4. **ADL:** BIC lag selection on a single common sample, then Newey–West HAC errors. Reports
   Σ x-coefficients with its p-value and the long-run multiplier.
5. **Granger F-test** of the x lags, on the same rows as the ADL.
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

**Publication rule:** "x leads y by k" is shown only if all six criteria hold:
1. Prewhitened CCF significant at k.
2. The CCF at k is still significant, with the same sign, after dropping the two pairs that
   contribute most to it.
3. Placebo p < 0.05.
4. Granger p < 0.05.
5. No reverse dominance.
6. The same verdict on the sample without 2020–22.

Otherwise the app says "no robust lead" and still shows the evidence.

Criteria 2 and 6 were added after the first real-data run. "M1–M2 gap leads IP by 3 months" passed
the original four (r = 0.61, placebo p = 0.002, Granger p < 0.001). However, two pairs carried it:
April–May 2022 IP (the Shanghai lockdown) lined up with the January–February 2022 swing in the
gap, which is a Lunar New Year effect in unadjusted M1. Without those two pairs r = 0.33. The ADL
coefficients net to zero (long-run multiplier −0.01, p = 0.95), and the result fails outside COVID.

**Inference uses unsmoothed series.** In testing, running these tests on a 3-month moving
average shifted the estimated lead (a planted 3-month lead was estimated at 6 months), because
prewhitening an MA-smoothed series is imperfect. Smoothed series are display-only.

### 3.2 Nowcasts (`analytics/nowcast.py`)
- **Target:** FAI single-month growth (comparable basis). Predictors are tested one at a time
  against an AR(1) benchmark: government-bond net-financing impulse, new local bonds, NDRC
  approvals, credit impulse ex-government, and bonds + credit.
- **Impulse scaling:** for inference the impulses are scaled by the TSF stock 12 months earlier
  (`~impulse_stk`). Scaling by GDP (`~impulse`) leaves only about 30 observations, because NBS
  release pages give GDP only from late 2021; both versions are charted.
- **No January fill:** a Jan–Feb release is one observation, and its AR lag is December.
  Filling January from February would put the target inside its own lag.
- **GDP bridge:** quarterly averages of the first month of the quarter (IP, real retail, real
  FAI, credit impulse).
- **Ragged edge:** each predictor is lagged by its publication delay. MOF local bonds, PBoC credit,
  NDRC and NBS activity: 1 month. (MOF publishes 24–31 days after month end, after NBS activity
  data.)
- **Evaluation:** expanding window, each model against an AR(1) refitted on the same sample.
  Every model nests the benchmark, so significance uses the Clark–West test; Diebold–Mariano
  (HLN) is reported for reference. Estimation excludes 2020–21.
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

## 4. Validation groups (`validate/checks.py`)
- **Accounting identities:**
  - Loan stocks by sector and tenor (exact, PBoC credit table).
  - Local bonds: total = new + refinancing.
  - Local bonds: general + special = MOF's separately reported new total.
  - TSF components vs total (residual = PBoC's unpublished "other", WARN).
  - Published single-month retail sales vs the change in published YTD.
- **Cross-source reconciliation:**
  - PBoC table vs PBoC release text (loans, TSF). The text is PBoC's first release; the table is
    the current vintage, in which PBoC re-allocates flows across the months of a year. Monthly
    differences are therefore revisions, and are logged as WARN.
  - TSF annual total: the sum of the table's 12 monthly flows must equal the full-year figure in
    the December release, within half its printed precision (RMB 10bn, or 50bn when printed to
    0.1 trillion). A gap beyond rounding but within 0.1% of the total is PBoC's later revision of
    the annual figure, logged as WARN. For example, 2023 was first printed as 35.59 trillion, while the
    2024 report's "比上年少3.32万亿" implies 35.58 trillion, the table's figure. A larger gap is a FAIL.
  - TSF government bonds − MOF local net vs NPC central quotas.
  - TSF stock y/y.
- **Published y/y reproduction:**
  - Retail comparable single-month y/y, within a rounding bound propagated from the 0.1pp
    rounding of YTD growth.
  - M1/M2 from levels; M1 2025 uses PBoC's restated 2024 base.
  - Tier aggregates vs NBS interpretation text. The parser reads one-tier statements and the
    combined forms "二、三线…分别…X%和Y%", "一、二、三线…X%、Y%和Z%" and "由上月…转为…". Each
    number takes the most recent 上涨/下降 before it.
  - Naive-vs-published gap logs.
- **Coverage:** missing months per key series (January excluded for Jan–Feb releases).
- **Plausibility:** magnitude bounds on key series and rolling-median checks that catch a block
  in the wrong unit. The 2017–19 PBoC share block that the review found would FAIL here.
- **Physical proxies:** direction agreement of 3-month changes.

A FAIL blocks Pages deployment and shows a banner in the app.

## 5. Release calendar assumptions
- **NBS:** monthly activity around the 15th–17th of the following month, at 10:00 Beijing time.
  Quarterly GDP comes with the March, June, September and December activity data.
- **PBoC:** credit data around the 10th–15th.
- **MOF:** the local-bond report, mid-to-late month.
- **NDRC:** press conferences, irregular, roughly monthly.
- **Refresh schedule:** the scheduled refresh runs at 10:20 and 18:20 Beijing time.
