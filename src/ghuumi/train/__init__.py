"""Training: two paths, one command.

``ghuumi train configs/train/lora-qwen.yaml``     adapt a strong open model
``ghuumi train configs/train/pretrain-nano.yaml`` train a model from zero

**Which one do you actually want?** Almost always the first. Pretraining a
competitive model costs millions of GPU-hours; fine-tuning one costs an evening
on the GPU you already own. Fine-tuning changes *style, format, and domain
knowledge expression* — which is what nearly everyone actually needs.

The from-scratch path exists for two reasons: it makes every mechanism visible
(there is no framework hiding the optimizer step), and it is the code path that
scales to real pretraining if you ever have the compute. Same file, same loop,
more GPUs.

Distillation feeds the first path: ``ghuumi distill`` (``distill.py``) turns a
strong teacher's graded answers into training data, and
``ghuumi train configs/train/distill-gsm8k.yaml`` fine-tunes a small, fast
student on it.

Measure before either path: ``ghuumi curriculum run configs/curriculum/<phase>.yaml``
(``curriculum.py``) scores one area with its benchmarks and collects references
for it, repeatably and without changing any weights.

See docs/TRAINING.md for the full walkthrough.
"""
