# Known limitations

Stated plainly so they are not mistaken for findings.

## Data access
- **NBS history starts in November 2021.** The NBS data portal blocks cloud IPs (403), and the
  release-page archive begins in November 2021. NBS-based lead/lag tests and nowcasts therefore have
  roughly 55–60 monthly observations: low power, and wide uncertainty on every estimate. PBoC credit
  data reaches back to 2012.
- **The national real estate climate index** (国房景气指数) is parsed where the property release
  text still states it. Recent releases omit it, so the series stops there; it is not proxied.
- **MOF's monthly local-bond release ends at September 2025** in the column the tracker crawls.
  Later local-bond issuance is missing until the new location is found; the TSF government-bond
  line (PBoC) continues and is used for the fiscal impulse.
- **Debt-swap issuance is a proxy** (refinancing special bonds since 2024-11); MOF does not state it.
- **Bond-by-bond records (ChinaBond / CELMA) are not ingested yet.** Both sites need
  interactive queries; there is no stable bulk endpoint. Local-bond data comes from MOF's
  monthly release text, which gives aggregates by type. Three consequences:
  - Debt-swap refinancing is separated only where MOF states it.
  - New special bonds used for swaps (up to RMB0.8trn a year) cannot be separated.
  - The "MOF vs bond-level sum" reconciliation is NOT_TESTABLE.
- **Central government bonds by type** (regular / ultra-long special / bank-recap special) need
  MOF issuance announcements or ChinaBond records; that fetcher is not yet built. Implied central
  net financing is derived instead, as PBoC TSF government bonds minus MOF local net issuance.
- **NDRC approvals effectively end in April 2021.** Parsing every press-conference transcript NDRC
  still hosts (2019 onward; older list pages return 404) yields only 7 monthly approval statements
  (2019-05 to 2021-04). NDRC stopped reporting monthly approval totals after that. The series is
  kept for the record, but the NDRC-to-FAI lead/lag test and nowcast cannot run on current data,
  and the app says so instead of fitting a model to 7 points. Possible replacements are
  provincial key-project lists, special-bond project disclosures and the online approval platform;
  none is ingested yet.
- **Provincial key-project lists and the national online approval platform are not ingested.**
  The project-type ("six networks") classifier is built and unit-tested, but it has no
  project-level input until those sources (or special-bond prospectus project lists) are
  scraped. Its audit precision/recall cannot be reported until then.
- **PBoC table data carries no publication history.** The statistics tables are re-published in
  place, so their rows get the retrieval date as vintage (`vintage_from_retrieval`). First-release
  monthly TSF and loan figures exist only in the release text (`tsf_text.*`, `loans_text.*`).
  They differ from the current table by PBoC's within-year re-allocation, which is up to about
  RMB 50bn a month, and RMB 134bn for April 2024. Annual totals agree.
- **TSF components** do not sum to the total by design (PBoC's unpublished "other" category). The
  identity is reported as WARN with the residual.
- **Physical proxies:** cement, steel and power come from NBS. Excavator sales (CCMA) and rebar
  apparent demand (Mysteel, paywalled) are not ingested.
- **Network policy:** the build environment used during development could not reach any
  Chinese official host. Real data is fetched by the GitHub Action on GitHub's runners;
  `source_status.json` and the app's Data & sources tab show exactly what each build reached.

## Method
- **IP has no tracker seasonal adjustment** (NBS publishes no levels); NBS's own SA m/m is shown.
- **FAI deflator:** PPI is used as a proxy for the FAI deflator.
- **The Jan/Feb split is a convention**; January and February individually are unobservable
  for NBS activity data.
- **Tier home-price aggregates are simple means of city indices,** matching NBS commentary;
  they are not transaction-weighted.
- **GDP sector contributions are a tracker approximation:** prior-year nominal share × sector
  real growth. NBS-published expenditure contributions are annual only in the portal.
- **Nowcast evaluation is pseudo real-time:** latest-vintage data with a replicated publication
  ragged edge. True real-time evaluation needs vintage history, which accumulates from the first
  build. Until then, revisions make the evaluation optimistic.
- **Break tests detect one unknown break** (Quandt–Andrews sup-F) plus Chow tests at known
  dates; multiple-break estimation (Bai–Perron) is not implemented.
- **Sample sizes:** PBoC credit data from 2012–15 onward is long, but NBS targets start in
  November 2021 (about 55–60 months), and lead/lag tests need ≥ 36 jointly observed months. With
  12 candidate lags, power is limited. The placebo and reverse-direction criteria guard against
  false positives, not false negatives: "no robust lead" is not evidence of no relationship.
- **Seasonally adjusted series revise** (two-sided X-11); they are not real-time signals.
- **Vintages before 19 Oct 2026 are partly estimated:** NBS archive pages re-dated in 2023, and
  PBoC tables stamped with the retrieval date. True first-release vintages accumulate from now on.
- **Quotas:** the 2026 NPC quotas are transcribed from a press report of the Government Work
  Report (marked unverified) until checked against the MOF budget report. The 2026 split of the
  deficit between central and local general bonds is not yet entered.
