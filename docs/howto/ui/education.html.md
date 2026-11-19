# `ui\education.html`

> The Education page, served at `/education`: what the model knows, per area, from its benchmark results.

**Read this when:** you change the page's layout or its pickers.

---

## What it does

- A header with the **setup** picker (which model and settings to show) and **Compare with** (another setup, drawn as a yellow marker).
- An empty-state card shown when no results are found, naming the folders searched (`education.runs_dirs`).
- One section per benchmark family, filled in by [`education.js`](education.js.md).
- Reached from the chat page's sidebar link "Education: what the model knows".

## How to open it

```powershell
ghuumi serve -c qwen36      # then open http://127.0.0.1:8080/education
```

The page reads results only; it works while a benchmark is running (from another window), and costs no model tokens.

## Related files

- [`education.js.md`](education.js.md), [`education.css.md`](education.css.md).
- [`../src/ghuumi/server/app.md`](../src/ghuumi/server/app.md): serves `/education`.
