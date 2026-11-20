# `src\ghuumi\autonomy\sources.py`

> Autonomous pull of openly licensed works from OpenAlex.

**Read this when:** a work is pulled that should not be, or one you expected is skipped.

---

## What it does
`pull_sources` keeps a work only with an allowed open licence, an ungated page, no AI-generation disclosure, the same host after redirects and at least 1500 characters of text. It fetches through the SSRF-safe `PublicWeb` and stops on rate limiting. It does not use the reviewed academic catalogue.

See [docs/AUTONOMY.md](../../../../AUTONOMY.md) for the whole flow.

