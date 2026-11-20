# Autonomy: pulling sources and training itself

Off by default. When `autonomy.enabled` is true, `ghuumi autonomy run` performs one cycle:

1. **Pull** open-access works for your `autonomy.topics` from OpenAlex. A work is kept only if its licence is in `autonomy.licenses` (CC0, CC-BY, CC-BY-SA, public domain), its page loads without a gate, it is not disclosed as AI-generated, and it stays on the same host after redirects. Fetching uses the SSRF-safe fetcher.
2. **Index** the corpus into a new documentation index and promote it immediately (retrieval is how the agent learns facts).
3. **Build examples** with the *base* model (never an adapter, to avoid feedback loops). Each example needs a quote of at least 6 words that appears verbatim in its passage, otherwise it is dropped.
4. **Train** a LoRA adapter once `min_new_examples` new training examples exist.
5. **Gate**: evaluate base and adapter on `gate_suite`. The adapter is promoted only if it scores at least base minus `max_regression`. Otherwise it is kept but not used.

Fine-tuning teaches format and grounded citing, not facts. Facts come from the index.

```yaml
autonomy:
  enabled: true
  topics: ["rust ownership", "godot scripting"]
```

Commands: `ghuumi autonomy pull` (pull and index only), `ghuumi autonomy run [--loop] [--train/--no-train]`, `ghuumi autonomy status`.

Notes:
- This is separate from the human-reviewed academic catalogue, whose review gate is unchanged.
- Training works only on the `transformers` backend (or via `autonomy.train_template`) and loads the whole model on GPU 0, so stop the serving process first.
- Promoted indexes and adapters apply the next time the CLI loads its config (`apply_promoted`); restart a running server to pick them up.
- State lives under `autonomy.home` (default `~/.local/share/ghuumi/autonomy`).
