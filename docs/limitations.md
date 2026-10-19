# Known limitations

Stated plainly so they are not mistaken for findings.

## Data access
- **NBS history starts in November 2021.** The NBS data portal blocks cloud IPs (403), and the
  release-page archive begins in November 2021. NBS-based lead/lag tests and nowcasts therefore have
  roughly 55–60 monthly observations: low power, and wide uncertainty on every estimate. PBoC credit
  data reaches back to 2012.
- **The national real estate climate index** (国房景气指数) no longer appears in the monthly property
  release text and is reported as missing rather than proxied.
- **Bond-by-bond records (ChinaBond / CELMA) are not ingested yet.** Both sites need
  interactive queries; there is no stable bulk endpoint. Local-bond data comes from MOF's
  monthly release text, which gives aggregates by type. Three consequences:
  - Debt-swap refinancing is separated only where MOF states it.
  - New special bonds used for swaps (up to RMB0.8trn a year) cannot be separated.
  - The "MOF vs bond-level sum" reconciliation is NOT_TESTABLE.
- **Central government bonds by type** (regular / ultra-long special / bank-recap special) need
  MOF issuance announcements or ChinaBond records; that fetcher is not yet built. Implied central
  net financing is derived instead, as PBoC TSF government bonds minus MOF local net issuance.
- **NDRC approvals are not a regular series.** NDRC states monthly approval counts and amounts
  in press conferences irregularly. Months without a statement are gaps, not zeros. NDRC
  approvals cover only projects needing central approval, a small and lumpy slice of investment.
- **Provincial key-project lists and the national online approval platform are not ingested.**
  The project-type ("six networks") classifier is built and unit-tested, but it has no
  project-level input until those sources (or special-bond prospectus project lists) are
  scraped. Its audit precision/recall cannot be reported until then.
- **TSF components** do not sum to the total by design (PBoC's unpublished "other" category). The
  identity is reported as WARN with the residual.
- **Physical proxies:** cement, steel and power come from NBS. Excavator sales (CCMA) and rebar
  apparent demand (Mysteel, paywalled) are not ingested.
- **Network policy:** the build environment used during development could not reach any
  Chinese official host. Real data is fetched by the GitHub Action on GitHub's runners;
  `source_status.json` and the app's Data & sources tab show exactly what each build reached.

## Method
- **IP has no tracker seasonal adjustment** (NBS publishes no levels).
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
- **Sample sizes:** monthly history from about 2012 gives about 150 observations, fewer for
  NDRC. Lead/lag tests with 12 candidate lags have limited power. The placebo and
  reverse-direction criteria guard against false positives, not false negatives.
- **Quotas:** the 2026 NPC quotas are transcribed from a press report of the Government Work
  Report (marked unverified) until checked against the MOF budget report. The 2026 split of the
  deficit between central and local general bonds is not yet entered.
