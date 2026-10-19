# Independent review and resolutions

The brief requires an independent reviewer to audit the work before handover, with the review and
its resolutions delivered with it. Two review rounds were run by a separate agent that had read
access to the repository and the raw archive and no write access. Between the rounds, validation on
real data surfaced further defects. All three are recorded here. Commit hashes refer to branch
`claude/china-activity-tracker`.

## Round 1 (audit of commit 65daeff)

The reviewer's summary was that "the committed data contains several plainly wrong numbers that
reach dashboard charts", and that none of the analytics had yet run on real data.

| # | Severity | Finding | Resolution | Commit |
|---|---|---|---|---|
| 1 | Critical | TSF flows for 2017–19 were structure shares (%), not RMB bn: the share block overwrote the flow block. | The parser reads unit blocks and keeps only 亿元 blocks; a later restated block wins (flag `restated`); a magnitude sanity check is added (median abs monthly flow ≥ 1,000 bn). Plausibility checks would now FAIL on a block in the wrong unit. 2017–19 monthly TSF is now 868–4,679 bn, and the 2019 total is 25,674 bn. | 676dfd4 |
| 2 | Critical | NBS periods were taken from URL dates re-set in the 2023 site migration (Q1-2022 GDP was stored as Q1-2023). | `title_period` takes the year from the release body or its release-date line; rows whose vintage precedes their period are rejected and counted in `source_status.json`. Real-data check: 0 fetched rows with vintage < period. | 676dfd4, 50b4fbd |
| 3 | Critical | PBoC release-text periods were taken from the first date in the page text (June-2025 M2 stored as Jan-2023). | The period comes from the report title. The store was reset and rebuilt from raw, and `append_fetched` replaces rows parsed from any re-parsed raw file, so stale rows cannot persist. M2 y/y for 2023-01 is now 12.6, and 2025-06 is 8.3. | 676dfd4 |
| 4 | Major | "前两个月" was not recognised, so Jan–Feb blocks were lost. | 两 was added to the period regexes. February single-month text flows are now differenced from January's single-month block, since the January report has no YTD block. | 50b4fbd, f6d4352 |
| 5 | Major | The credit-impulse GDP denominator summed four non-consecutive quarters. | `gdp_4q_monthly` reindexes to a full quarterly range and requires 4 consecutive quarters. Inference uses a GDP-free impulse (Δ12 of the 12m flow / TSF stock at t−12). | 676dfd4 |
| 6 | Major | The vintage store was not usable for real-time work. | PBoC table rows are flagged `vintage_from_retrieval` (tables are re-published in place). First-release PBoC figures are kept as separate `*_text` series with publication dates. Archived NBS pages get period + 17 days, flagged `vintage_estimated_archive`. Each derived row takes the as-of maximum of its inputs' vintages for that period. A re-parse replaces its own earlier rows. | 676dfd4 |
| 7 | Major | Nowcast leakage: January fill made February's lag equal the target; MOF L = 0; positional shifts; DM test on nested models. | No January fill (February's AR lag is December); time-based publication lags with MOF L = 1; Clark–West for nested models, with DM reported for reference. | 676dfd4 |
| 8 | Major | Lead/lag artefacts: a 2-month February difference copied into January; BIC compared across unequal samples. | Full monthly index with nothing filled; time-based differences; BIC on one common sample. A later fix makes a target's own lags skip months it never has, so the ADL sample no longer collapses. | 676dfd4, 586aa8a |
| 9 | Major | M1 y/y was computed across the 2025 redefinition (+62% to +80%). | The 2025 y/y is rebuilt on PBoC's restated 2024 M1 base, parsed from the table footnote; the `recon:m1_yoy` check compares it with the published figure. | 50b4fbd |
| 10 | Major | Own SA ran on YTD-differenced levels with annual sample-redraw jumps; no retail SA. | SA runs on an implied comparable-basis level, `L_t = L_{t−12}(1+g_t)`, published as `~clevel`; retail SA is added. The charts state that seasonal factors are two-sided and revise. | 676dfd4, f6d4352 |
| 11 | Major | NBS parser gaps silently dropped data (IP "实际", retail full year, older property wording, high-tech vs manufacturing FAI, PPI months). | All fixed with unit tests. Real-data validation later found more (see the next section). Coverage checks now WARN on any missing month. | 676dfd4, 5d41bcc |
| 12 | Major | No debt-swap split existed. | MOF never states swap amounts. `lgb.refi_swap_proxy` (refinancing special bonds from 2024-11) is labelled as a proxy in the app, the registry and the limitations, and flagged `proxy_not_reported_by_mof`. | 676dfd4 |
| 13 | Major | Vacuous checks (`lgb_new`, `ytd_roundtrip`); the absolute tolerance missed finding 1; FAILs did not block publishing. | `lgb_new` now compares with MOF's separately reported new total; `ytd_roundtrip` was dropped; plausibility and coverage groups were added; Pages deploys only on success, and the app shows a banner when any check FAILs. | 676dfd4 |
| 14 | Major | Analytics had never run on real data. | They now run on every refresh. Current results: no robust lead; the FAI nowcast does not beat AR. | 676dfd4 onward |
| 15 | Minor | 2012–14 household loan identity; housing base label; dead chart ids; steel proxy label; truncated housing lineage; contradictions in limitations.md; credit.py comment. | All fixed. The housing level is now 2022 average = 100, with gaps shown as gaps. | 50b4fbd, 676dfd4, 5d41bcc |
| B1 | Major | X-11 SA and SAAR are two-sided, so the "one-sided only" claim was false. | Methods §2.3 is corrected: only y/y, trailing means and 12m sums feed real-time signals; SA momentum is labelled as revising and is never used as a predictor. | 676dfd4, f6d4352 |
| B2 | Major | Catalog provenance was per series. | Per-point `src`/`si` arrays: every plotted value links to its own raw file and URL. | 676dfd4 |
| B3 | Minor | The COVID special CGB would be classified as bank recapitalisation. | `cgb_covid_special` category added. | 676dfd4 |
| B4 | Minor | Refinancing special bonds used for swaps fall into regular refinancing. | Handled by the labelled swap proxy (finding 12). | 676dfd4 |
| B5 | Minor | NBS FAI may now be price-adjusted. | **Disputed:** the release tables state "此表中速度均未扣除价格因素" (growth rates are not price-adjusted). The PPI deflator proxy stays. | — |
| B6 | Minor | `chain_level` treated a gap as zero change. | Gaps are masked and the chain resumes after them. | 676dfd4 |
| B7 | Minor | `proxy_agreement` used positional rolling across January gaps. | Each series is put on its own observation calendar first. | f6d4352 |
| B8 | Minor | Chow p-values are not HAC. | Not changed; documented in limitations (break evidence is indicative). | f6d4352 |
| B9 | Minor | GDP contributions use nominal shares. | Documented as an approximation. | — |

## Findings from real-data validation after round 1

The first real-data builds after the round-1 fixes surfaced these. Each was traced to raw text
before it was fixed.

| Finding | Evidence | Fix | Commit |
|---|---|---|---|
| A refresh run from superseded code failed on a rebase conflict with the store reset. | Job log: modify/delete conflict on `observations.parquet`. | The refresh discards its data when pipeline code changed on the branch while it ran. | c06e6d3 |
| Tier-3 existing-home m/m for May 2023 FAILed (computed −0.19, published −0.3). | Raw text: "二、三线城市二手住宅销售价格环比由上月持平分别转为下降0.3%和0.2%"; the parser gave tier 3 the tier-2 figure. | Multi-tier statement parser (二、三线 / 一、二、三线, transitions, sign carried forward); "三线" no longer matches inside "二、三线". | 5d41bcc |
| TSF table vs release text FAILed by 15–134 bn a month. | Annual sums of the table equal PBoC's published annual totals (2021 31.34, 2022 32.01, 2023 35.58, 2024 32.26 trn). | The text is first release and the table the current vintage, so monthly gaps are revisions (WARN). A new hard check compares the table's annual sum with the December release; 2023's 10.1 bn gap matches PBoC's own later revision (35.59 → 35.58 trn) and is WARN; a gap above 0.1% would FAIL. | 5d41bcc, b9fc58c |
| September PBoC text blocks were dropped. | "前三季度" mapped to no period. | Quarter phrases are resolved before month counts. | 5d41bcc |
| December PBoC reports were never fetched. | The annual title "2025年金融统计数据报告" has no month. | The crawl accepts titles without a month; the bare year in the body is read as Jan–Dec. | 5d41bcc |
| January 2023 household loans: 1,091 bn from stock differences vs 257 bn published. | The 2023 credit table covers consumer-finance and other newly included institutions; the 2022 table does not. | Break `PBOC_SCOPE_2023` registered; the January 2023 flow uses the release-text figure, flagged. | 5d41bcc |
| Retail YTD missing 2022-03/06, 2023-06, 2026-06; PPI missing 2025-07, 2026-03. | Quarter-end releases use "一季度/上半年,…"; PPI used "由上月下降0.9%转为上涨0.5%" and a sentence without 全国. | Parsers extended, with tests. | 5d41bcc |
| Published M1/M2 y/y missing April–December 2024. | "狭义货币(M1)…同比下降5%": the parser required 增长. | Sign read explicitly; M2 is no longer dropped with M1. | 586aa8a |
| 2026 money supply missing. | The current-year table has a 12-month header and shorter rows. | Partial rows are padded. | 586aa8a |
| Lead/lag regression samples collapsed (n ≈ 29–32 for IP). | Every own lag spanning a January was missing. | A target's own lags skip calendar months it never has; the indicator's lags stay calendar months. | 586aa8a |
| "M1–M2 gap leads IP by 3 months" passed all four original criteria (r = 0.61, placebo p = 0.002, Granger p < 0.001). | Two pairs carried it: April–May 2022 IP (Shanghai lockdown) against the January–February 2022 Lunar-New-Year swing in M1. Without them r = 0.33; the ADL coefficients net to zero (long-run multiplier −0.01, p = 0.95); it fails on the sample excluding 2020–22. | The verdict now also requires surviving removal of the two most influential pairs, and the same verdict without 2020–22. The claim is withdrawn: no robust lead. | 760b9dc |
| GDP quarters missing (2022-Q2, 2023-Q1, 2024-Q4, 2025-Q3/Q4, 2026-Q2). | Those national releases are titled "…经济运行…" or carry no period phrase. | NBS "国内生产总值初步核算结果" releases are also parsed, the title match is widened, and 二/三/四季度 phrases were added. | f6d4352 |

## Round 2 (audit of commit f6d4352)

The reviewer worked on a copy of the repository and rebuilt everything from the raw archive, both
incrementally and from an empty store. Its spot checks against official figures all matched: GDP
levels and growth 2021–25, FAI 2021–24, retail, IP, PPI, M1/M2 2018+, and TSF annual totals.
Round-1 verdicts across the 15 findings and 9 suspected items: 14 resolved, 6 partially
resolved, 1 disputed correctly (FAI is not price-adjusted), 2 not resolved (the latent chain-level
gap and the non-HAC Chow test, both documented), and 1 documented approximation. The partial ones
(5, 6, 12, 13, 14 and B4) are covered by A2–A6, A14 and B4 below. The reviewer's summary was that the conclusions ("no robust lead"; "the FAI nowcast
does not beat AR") are computed correctly, but that five defects still put wrong numbers on charts
or in the vintage store.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| A1 | Major | TSF flows were spliced across a definition change at 2017-01 (2016: 17.8 trn on the old definition; 2017: 26.2 trn on the current one), creating a 2017–18 hump in the impulse. Not in the break register. | Break `TSF_DEF_2017` registered. Rolling sums and impulses use flows from 2017-01. Stock tables are never restated (jumps at 2018-01, 2019-01, and a scope change at 2023-01), so stock y/y from levels is computed only within a segment. The impulse denominator is the comparable year-ago stock, S_t / (1 + published y/y). `recon:tsf_stock_yoy` went from WARN (mean gap 1.94 pp) to PASS (81 months, mean 0.03 pp). The impulse now starts in 2018-12. |
| A2 | Major | The GDP denominator of the impulse was forward-filled without limit (one 4Q sum used for 24 months). | The fill is capped at the current quarter (2 months after the release month); beyond that the denominator is missing. Unit test added. |
| A3 | Major | Derived vintages gave look-ahead: 159 rows were dated before their period (split January rows took December's release date), and two-sided SA rows took per-period dates. | A derived month the input never has takes the vintage of the input's next observation (January takes the Jan–Feb release date, e.g. 2024-01 → 2024-03-22). Two-sided transforms (SA and everything built on it) take the latest input vintage. There is a floor at the day after the period end. A new check, `vintage:order`, fails on any row dated on or before its period end; it passes with 0 rows. |
| A4 | Major | The debt-swap proxy was shown as % of the RMB 2 trn swap quota (140% in 2025). | It is shown in RMB bn only. The chart and limitations say it includes ordinary rollover of special bonds and overstates the swap. |
| A5 | Major | Build output depended on the previous store: `lgb.new_total~impulse_stk` was read before it was computed. | The deriver ignores stored derived rows and runs its steps in dependency order (bonds before credit). Verified by a replay from an empty store. |
| A6 | Major | Local-bond history was lost: `lgb.new_total` began in 2021-11 although MOF states the new total from 2018-10. | `lgb.new_total` uses MOF's stated figure, falling back to general + special. The rebuild then exposed a parser gap: the 2022–23 wording "发行新增地方政府债券", and January 2020's "全部为新增债券", were not matched, leaving 9 months missing. Both are fixed, with tests, as is the "全部为再融资债券" form (January–February 2021). The series now runs 2018-10 to 2025-09, missing only 2018-12 (not on MOF's list pages). The "new local bonds → FAI" pair now runs: no robust lead (best lead 9 months, r = −0.40, placebo p = 0.26). The nowcast model still lacks data (43 common months), because MOF's monthly release ends at 2025-09. |
| A7 | Minor | December infrastructure FAI was never parsed ("比上年增长"). | Regex fixed. Coverage checks were added for infrastructure and manufacturing FAI and pass, with December present every year. |
| A8 | Minor | The pre-2021 M1–M2 gap from table levels differs from published y/y by up to ~1.2 pp. | The chart subtitle states the source switch and the size of the discrepancy; points are flagged `from_levels`. |
| A9 | Minor | NBS retail m/m SA has only 5 observations (the revision table is not parsed). | Documented in limitations. |
| A10 | Minor | 621 rows for Nov–Dec 2022 carried the migration date as vintage. | URL dates equal to the migration date are never treated as publication dates; those rows get an estimated vintage, flagged. |
| A11 | Minor | The app and docs disagreed with the code: "four tests", "need 48", README "DM tests", nowcast docstring L = 0, and the methods note on lags. | All aligned. The app lists all six criteria. Methods §3.1 describes the own-calendar lags for the target and their consequences, including the reviewer's size simulation (6.5% at a fixed lag). |
| A12 | Minor | The stated reason for withdrawing "M1–M2 → IP" overclaimed: the influence check alone does not reject it (r = 0.33 against a band of 0.28). | Corrected in methods §3.1 and above. The claim fails on stability: without 2020–22 the best lead moves to 10 months with the opposite sign. It is also disclosed that the extra criteria were added after this result was seen. |
| A13 | Minor | FAI SA m/m showed extreme January values from the even Jan/Feb split. | January is masked; February shows the Jan–Feb average against December, flagged. A remaining large February 2026 value is in the official data: comparable growth goes from −15.1% (December 2025) to +1.8% (Jan–Feb 2026). |
| A14 | Minor | Data was committed even when a check FAILed. | A failed build commits only the raw archive. The store and app data stay at the last good build. |
| A15 | Minor | `title_period` could fall back to the URL year for half-year titles on migrated pages. | The release-date line is tried first; on a migrated page with no year in the title or text, the period is rejected rather than guessed. |
| A16 | Minor | February `retail.level` rows had no Jan–Feb flag. | Flagged `janfeb_combined` at ingest, together with February `retail.yoy` and `ip.yoy`. |
| B1 | Minor | `influence_check` ignored sign. | It drops the pairs contributing most in the direction of the correlation. |
| B2 | Minor | The Granger criterion used a homoskedastic F test. | The criterion is the HAC Wald test; the F test is still reported. |
| B3 | Minor | `survives_ex_covid` accepted a robust lead at any lag. | It now requires the same lead. |
| B4 | Minor | Prewhitening mixes calendars. | Documented, with the simulation result. |
| B5 | Minor | `housing.chain_level` chains across a gap as zero change (latent: no gaps today). | Documented in limitations. |
| B6 | Minor | A crash could leave a partial store committed. | Covered by A14. |
| B7 | Minor | The `tsf_annual` WARN band (0.1%) was wide. | Tightened to 0.05% (about RMB 18 bn). |

Effect on the conclusions: none of the seven runnable lead/lag pairs shows a robust lead. The FAI
nowcast has 17 out-of-sample months (2025-04 to 2026-09), a relative RMSE against AR of
1.000–1.005, and Clark–West p of 0.40–0.56. The correct reading is "no detectable gain, with very
low power", not "the indicators are useless".

GDP coverage, flagged by the reviewer as untestable offline, is resolved. The live refresh fetched
NBS's preliminary-accounting GDP releases. Their tables carry the full quarterly real y/y and SA
q/q history at each release's vintage, so real GDP y/y and SA q/q now run continuously from
2017-Q1 to 2026-Q3 (39 quarters, previously 11–14), and nominal YTD has no gaps since 2021. The
GDP bridge now runs for the credit-impulse model: over 19 out-of-sample quarters it does not beat
AR (relative RMSE 1.17, Clark–West p = 0.84). The models with monthly NBS indicators still have only
about 15 common quarters, because the NBS monthly releases start in late 2021.
