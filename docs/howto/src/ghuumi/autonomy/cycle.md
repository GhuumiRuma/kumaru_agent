# `src\ghuumi\autonomy\cycle.py`

> One pull, index, build, train, gate and promote cycle, plus the loop.

**Read this when:** a cycle does not train, an adapter is or is not promoted unexpectedly, or you change the gate.

---

## What it does
`run_cycle` promotes a fresh retrieval index at once, trains only after `min_new_examples` new training examples exist, then evaluates base and adapter on `gate_suite`. The adapter is promoted only if it scores at least base minus `max_regression`. `run_loop` repeats every `interval_s`. Training requires the transformers backend or `train_template`.

See [docs/AUTONOMY.md](../../../../AUTONOMY.md) for the whole flow.

