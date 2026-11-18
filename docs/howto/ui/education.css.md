# `ui\education.css`

> Styles for the Education page, on top of `styles.css`: bars, interval bands, chance ticks and compare markers.

**Read this when:** a bar, band or marker is hard to read.

---

## What it does

- Reuses the chat UI's colours and fonts (`styles.css`, loaded first).
- **Bar:** an inset fill for accuracy; a translucent gray band for the 95% interval, taller than the fill so it shows around it; a thin tick at chance; a yellow marker for the compared setup.
- **Dimmed rows** (fewer than 10 answers) and the live progress bar.
- `body.education [hidden] { display: none !important; }`: the cards use `display: flex`, which would otherwise override the `hidden` attribute.
- Widths do not animate: a bar changes only when new answers arrive.

## Related files

- [`education.html.md`](education.html.md), [`education.js.md`](education.js.md), [`styles.css.md`](styles.css.md).
