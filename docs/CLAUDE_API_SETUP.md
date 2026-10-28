# Claude API setup

## 1. Get an API key

1. Sign in at <https://console.anthropic.com>.
2. **Plans & Billing** – add credits or a payment method. Without credit, requests fail with
   *"Your Anthropic account has no available credit"*; the app shows exactly that message.
3. **API keys** – *Create key*. Copy it (starts with `sk-ant-`).

A Claude.ai (chat) subscription is separate from API billing and does not provide an API key.

## 2. Give the key to the app

Pick one:

| Method | How | Notes |
|---|---|---|
| **Settings screen (recommended)** | ⚙ > Claude API > paste > **Save key** | Stored in Windows Credential Manager, encrypted for your Windows account |
| Environment variable | `setx ANTHROPIC_API_KEY "sk-ant-..."` then restart the app | Takes precedence over the stored key |
| `.env` file (development) | copy `.env.example` → `.env` | Only read when running from source; git-ignored; never bundled |
| `ant auth login` profile | Anthropic CLI | Used automatically by the SDK when no key is stored |

The key is never written to `settings.json`, never logged (log lines are redacted), and only a
masked hint such as `sk-ant-…a1b2` is ever displayed.

Click **Test connection**: it calls the Models API (no tokens billed) and reports either the
model name or a specific problem (invalid key, no permission for the model, network, ...).

## 3. Model and generation settings

| Setting | Default | Notes |
|---|---|---|
| Model | `claude-opus-5-5` | Best quality. `claude-sonnet-5-5` is faster; `claude-haiku-5-5` is fastest/cheapest – useful if live latency matters more than nuance. Any model ID can be typed in. |
| Reasoning effort | Low | `low`/`medium`/`high`. Replaces temperature (see below). Low keeps live answers fast. |
| Answer length | Medium (~60 s) | Short ~80 words, Medium ~150, Long ~220. Profile setting overrides. |
| Answer mode | Concise | Concise / Detailed / STAR / Technical / Beginner. Behavioural questions default to STAR. |
| Max tokens | 16000 | Upper bound including Claude's internal reasoning. |
| Timeout / retries | 60 s / 2 | The SDK retries 408/409/429/5xx and connection errors with backoff. |
| Refusal fallback | On | On Opus 5.5 / Sonnet 5.5, a safety-declined request is retried server-side on Anthropic's recommended fallback model (`fallbacks: "default"`). |

**Why no temperature?** Since Messages API version `2026-10-21`, requests that include
`temperature`, `top_p`, or `top_k` are rejected on every model. Reasoning effort is the
supported control.

## Costs

Usage and an estimated cost are shown under the answer cards. Prices used for the estimate
(USD per million input/output tokens): Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5
$0.10/$0.50. Your profile is prompt-cached, so repeated questions in one session re-read it at a
fraction of the input price. A typical answer costs roughly $0.01–0.03 on Opus 5.5.

## What is sent to Anthropic

The detected question, up to N recent transcript lines (configurable, default 6), your active
profile (resume text, job description, skills, projects, experience), and fixed instructions.
Nothing is sent until a question is detected, typed, or practice/auto-fill is used.
