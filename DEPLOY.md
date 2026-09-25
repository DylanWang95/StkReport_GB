# StkReport_GB: historical-close deployment

This bundle is prepared from the supplied `index.csv` and the September 24
report-script revision. The CSV contains September 1–24, 2026 closes; it is not
a year-to-date backfill. Weekend carry-forward rows, the US September 7 market
holiday, and the unfinished September 25 row were excluded.

Place these files in the public repository in **one commit**:

| Bundle file | Repository location |
| --- | --- |
| `market_report.py` | `market_report.py` |
| `history_store.py` | `history_store.py` |
| `data/historical_closes.csv` | `data/historical_closes.csv` |
| `main.yml` | `.github/workflows/main.yml` |
| `test-report.yml` | `.github/workflows/test-report.yml` (if replacing the existing test workflow) |

The main workflow needs `contents: write` to save newly validated closes. It
retains the previous once-a-Beijing-day email guard and serial execution. The
test workflow keeps `contents: read`, uses `SEND_EMAIL=false`, and never commits
data. Run it on the desired branch after upload and check the six Store/Output
lines and the final text. No email credentials are passed to the test job.

Current schedule is 16:35 America/New_York, as in the supplied main workflow.
The first live run needs the preceding trading day's close in the CSV. If the
latest saved date is older than four calendar days, the report stops rather
than using a stale denominator. A successful live run appends the day's close
to the public CSV; a data validation failure appends nothing. The first
deployment should be outside the scheduled trigger window so the complete
bundle is present before the next scheduled run.

For a later Wind backfill, export a fresh matrix with the same 13 columns and
run locally:

```text
python import_wind_closes.py index.csv data/historical_closes.csv --through YYYY-MM-DD --merge-existing
```

`--through` must be the latest fully settled trading date. Check exchange
holidays in any new date range before merging: this importer explicitly knows
only the September 7, 2026 US closure from the supplied sample. The merge
preserves dates outside the new Wind export and lets Wind supersede overlapping
Yahoo records. Review the CSV diff before committing it.

If `git push` from Actions is blocked by branch protection, allow the bot to
write this file through the repository's normal review path, or move the data
file to a dedicated branch. The script will still print its report, but the
next run cannot rely on unsaved closes until that write succeeds.
