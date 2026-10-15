"""Versioned mathematics shortlist grounded in the evaluator's published results."""

VERSION = "matharena-2026-10-05"
REVIEWED_AT = "2026-10-05"
SOURCE = "https://matharena.ai/models"
# Best published configuration per model, deduplicated. No endpoint aliases are
# substituted: a catalog entry must match the evaluated model's actual ID.
SHORTLIST = (
    ("openai/gpt-6.1-sol", "openai_gpt_61_sol", "max; Codex harness"),
    ("openai/gpt-6-astra", "openai_gpt_6_astra", "max; Codex harness"),
    ("openai/gpt-6-sol", "openai_gpt_6_sol", "max; Codex harness"),
    ("anthropic/claude-opus-5.5", "anthropic_opus_55_high", "high; Claude harness"),
    ("anthropic/claude-fable-5.1", "anthropic_fable_51_max", "max; Claude harness"),
    ("anthropic/claude-opus-5", "anthropic_opus_5_max", "max"),
    ("openai/gpt-5.6-sol", "openai_gpt_56_sol", "max"),
    ("openai/gpt-5.5", "openai_gpt_55", "xhigh"),
    ("qwen/qwen3.8-max", "qwen_qwen3_8_max", "published configuration"),
    ("openai/gpt-5.4-pro", "openai_gpt_54_pro", "xhigh"),
    ("anthropic/claude-fable-5", "anthropic_fable_5_max", "max"),
    ("moonshotai/kimi-k3", "moonshot_k3", "Think; Kimi harness"),
    ("x-ai/grok-4.7", "xai_grok_47", "xhigh"),
    ("meta/muse-spark-1.3", "meta_muse_spark_13", "published configuration"),
    ("meta/muse-spark-1.1", "meta_muse_spark_11", "published configuration"),
    ("anthropic/claude-opus-4.8", "anthropic_opus_48_max", "max"),
    ("deepseek/deepseek-v4.1-flash", "deepseek_deepseek_v41_flash", "Max"),
    ("openai/gpt-5.4", "openai_gpt_54", "xhigh"),
    ("x-ai/grok-4.5", "xai_grok_45", "published configuration"),
    ("openai/gpt-5.2", "openai_gpt_52", "xhigh"),
    ("google/gemini-3.8-flash", "gemini_gemini_38_flash", "published configuration"),
    ("google/gemini-3.1-pro-preview", "gemini_gemini_31_pro", "published configuration"),
)


def recommendations(models: list[dict], *, pinned: list[str] | None = None) -> dict:
    available = {row["id"] for row in models}
    selected = [item for item in SHORTLIST if item[0] in available][:20]
    records = [
        {
            "model_id": model,
            "evidence_url": "https://matharena.ai/models/" + page,
            "evaluated_setting": setting,
            "reason": "Published mathematical evaluation",
        }
        for model, page, setting in selected
    ]
    ids = [item["model_id"] for item in records] if pinned is None else [model for model in pinned if model in available]
    return {
        "version": VERSION,
        "reviewed_at": REVIEWED_AT,
        "source": SOURCE,
        "model_ids": ids,
        "evidence": records,
        "organization_override": pinned is not None,
        "method": (
            "Organization-selected shortlist. Published evaluation links are shown where available. "
            if pinned is not None
            else "MathArena published expected-performance order, best configuration per model, intersected with the live catalog. "
        )
        + "The evaluator combines observed and estimated results. "
        "Its harness, reasoning effort, tools, and budgets may differ from this app; "
        "these are candidates for mathematics, not measured performance of this deployment.",
    }
