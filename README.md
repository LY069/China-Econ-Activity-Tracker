# China Econ Activity Tracker

A China economic activity tracker built for analytical credibility. It covers official
statistics (GDP, industrial production, retail sales, fixed-asset investment, total social
financing, loans by sector and tenor, M1/M2, 70-city home prices by tier, property activity)
and high-frequency indicators (central and local government bond issuance by type, NDRC
project approvals). Every number in the app traces back to a dataset row and the raw
official response it was parsed from.

## Layout

```
cetracker/
  store.py            vintage-aware observation store (Parquet + DuckDB views)
  registry.py         series definitions; NBS indicators resolved by name from the portal tree
  fetch/              raw fetchers (content-addressed raw archive) and release-text parsers
  ingest.py           sources -> store rows, per-source status report
  transform/          YTD/Jan-Feb, comparable-basis y/y, X-13 + CNY SA, deflation,
                      credit impulse, bond pace vs quota, 70-city tiers
  derive.py           all derived series, with lineage
  analytics/          lead/lag tests (prewhitening, ADL-HAC, Granger, breaks, placebos),
                      pseudo real-time nowcasts (DM tests), project-type classifier
  validate/checks.py  identities, reconciliations, y/y reproduction, physical proxies
  build.py            ingest -> derive -> analytics -> validation -> site/data/*.json
config/               break register, NPC quotas, tiers, CNY dates, six-networks dictionary
site/index.html       interactive app (auto-refreshes data every 15 min)
site/tracker_standalone.html   single-file snapshot with data embedded
docs/                 methods note, validation report (generated), known limitations
tests/                unit tests + synthetic integration test with planted properties
```

## Running

```bash
pip install -r requirements.txt
python -m pytest -q                 # unit + integration tests
python -m cetracker.build           # fetch, derive, analyse, validate, export
python -m cetracker.build --offline # recompute from the existing store
cd site && python -m http.server    # open http://localhost:8000
```

The `refresh-data` GitHub Action runs the build twice daily (10:20 and 18:20 Beijing time),
commits new vintages and publishes `site/` to GitHub Pages from `main`.

## Documents
- `docs/methods.md`: every transformation, test and assumption
- `docs/validation_report.md`: generated on each build
- `docs/limitations.md`: what the tracker cannot do yet, and why
