# The submitted results

Six standard trials and three adversarial trials against the final task.

| Configuration | Trials | Reward |
|---|---|---|
| claude-code, `anthropic/claude-opus-5`, effort max | 3 | 0.000, 0.000, 0.000 |
| codex, `openai/gpt-5.6-sol`, effort xhigh | 3 | 0.000, 0.000, 0.000 |
| Adversarial (`/cheat`), claude-code | 1 | 0.000 |
| Adversarial (`/cheat`), codex | 2 | 0.000, both cut short by an OpenAI safety refusal |

Directories with `-voided` in the name are excluded execution failures, kept so the exclusions are
auditable. See [`../README.md`](../README.md) for the full index and
[`../../SUBMISSION.md`](../../SUBMISSION.md) for the analysis.

An earlier, discarded version of the task is in [`../trials-v1/`](../trials-v1). It is not the
submission.
