# `ui\education.js`

> The Education page's logic: fetch `/v1/education`, pick a setup, and draw one bar per area with its uncertainty, the chance level and, optionally, another setup to compare with.

**Read this when:** you add a benchmark family to the page, change how a bar reads, or a bar does not refresh.

---

## What it does

- **Fetches** `/v1/education` and groups measurements by **setup** (run label + model). The setup picker and the "Compare with" picker are remembered in `localStorage` (`ghuumi.education.setup`, `ghuumi.education.compare`).
- **One section per family**, in `ORDER`; titles, notes and chance levels come from `FAMILIES` (SuperGPQA and MMLU-Pro: 10%). MultiPL-E members are shown by language name (`LANGUAGES`). Unknown families still show, titled by name.
- **One row per area**: the bar's fill is the share answered right, a gray band is its 95% interval, a tick marks chance, and a yellow marker shows the compared setup with the difference in points. Rows with fewer than `FEW` (10) answers are dimmed: too few to read.
- **Live runs**: a benchmark that is unfinished and was updated in the last 10 minutes shows a progress bar (answered of planned) and the page polls every 30 seconds; otherwise every 5 minutes.
- Everything is built with `textContent`, never HTML strings: names in result files cannot inject markup.

## Adding a family

Add an entry to `FAMILIES` (`title`, `note`, `chance`) and its name to `ORDER`. Nothing else: areas come from the results.

## Related files

- [`education.html.md`](education.html.md), [`education.css.md`](education.css.md): the page and its styles.
- [`../src/ghuumi/server/education.md`](../src/ghuumi/server/education.md): where the numbers come from.
