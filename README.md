# GoushFi

GoushFi is an existing bilingual financial-management product built with Python and Streamlit. It brings income, expenses, projects, invoices, documents, savings, tax readiness, and optional cloud sync into one practical workflow.

For OpenAI Build Week 2026, we built and deployed **Financial Calm Brief**: a focused decision layer that turns financial data into exactly three prioritized actions. GoushFi predates Build Week; the competition work is the Financial Calm Brief feature, its safety contract, gated judging experience, tests, and deployment.

## Financial Calm Brief

Financial dashboards often show many correct numbers without answering the user's immediate question: **what should I do next?** Financial Calm Brief reduces that noise to three decisions.

The Build Week demo produces these synthetic, repeatable results:

1. Follow up `850.00 KWD` in open amounts.
2. Calm a `90.00 KWD` increase in Coffee / Outings.
3. Protect a positive `1,130.00 KWD` 90-day cash outlook.

The same financial facts appear in Arabic and English.

## Python owns the truth

The feature separates financial computation from language generation:

```text
GoushFi financial data
        |
        v
Deterministic Python analysis
        |
        v
Ranked fact packet + exactly three decisions
        |
        +----> Safe deterministic explanation
        |
        +----> Optional GPT-5.6 explanation
                    |
                    v
             Fail-closed validator
                    |
                    v
                Streamlit UI
```

- Python calculates every amount, decision, priority, title, and action.
- GPT-5.6 is an optional explanation layer. It receives approved decision IDs and explains why they matter.
- User-visible AI explanations may not introduce financial numbers.
- Unknown decisions, invalid JSON, changed order, missing decisions, or numeric explanation text are rejected.
- When the API is absent or a response fails validation, GoushFi uses the deterministic explanation.

The judging deployment intentionally runs without `OPENAI_API_KEY`, so it demonstrates the deterministic fallback without requiring a paid OpenAI API call. The optional integration defaults centrally to `gpt-5.6` and can be changed with the server-side `OPENAI_MODEL` environment variable.

## Judges' demo

Direct Financial Analyzer link:

**https://goushfi.goushkw.com/?page=assistant**

The judging feature is gated and fails closed:

- Only the approved web Demo account sees Financial Calm Brief.
- Public users continue to see the existing AI Brief.
- The iPhone WebView path continues to see the existing report.
- `GOUSHFI_BUILD_WEEK_FORCE_WEB` remains `0` in judging and production.

Demo credentials belong only in Devpost's private judges field. They are not stored in this repository or displayed in screenshots.

### Suggested judging flow

1. Sign in with the private Demo credentials supplied through Devpost.
2. Open the direct Financial Analyzer link.
3. Confirm that exactly three Financial Calm Brief decisions appear.
4. Switch between English and Arabic and compare the three amounts.
5. Resize to a mobile viewport and confirm the decision cards remain readable.
6. Note the fallback message: the demo works without an OpenAI API key.

## Synthetic demo data

The judging fixture is fully fictional and uses a fixed date and KWD amounts. It contains no real customer names, bank accounts, email addresses, tokens, attachments, or production financial records.

Source: [`tests/fixtures/financial_calm_brief_demo.json`](tests/fixtures/financial_calm_brief_demo.json)

Expected decision IDs:

- `follow_up_open_items`
- `reduce_category_spike`
- `protect_cash_outlook`

## Role of Codex and GPT-5.6

Codex was used as the engineering agent for the Build Week work: inspecting the existing analyzer, defining the narrow scope and acceptance tests, implementing the deterministic engine and constrained AI contract, isolating commits, running tests, deploying the gated web experience, and validating the live Arabic, English, public, Demo, mobile, and iPhone WebView paths.

GPT-5.6 is the optional explanation model behind the validated AI path. It is deliberately not trusted with financial arithmetic or decision selection. The deployed judging build keeps that path unconfigured and proves that the product remains useful and truthful through its deterministic fallback.

## Judging screenshots and video

- [English desktop](docs/build-week/screenshots/financial-calm-brief-en.jpg)
- [Arabic desktop](docs/build-week/screenshots/financial-calm-brief-ar.jpg)
- [Mobile 390 x 844](docs/build-week/screenshots/financial-calm-brief-mobile-390x844.jpg)
- [Under-three-minute video script and shot list](docs/build-week/VIDEO_SCRIPT.md)

## Run locally without OpenAI API

Requirements:

- Python 3.12
- `pip`

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

No OpenAI key is required for the deterministic Financial Calm Brief engine or its fallback explanations.

Optional server-only AI configuration:

```bash
export OPENAI_API_KEY="set-this-outside-the-repository"
export OPENAI_MODEL="gpt-5.6"
```

Never commit real secrets. The Build Week judging deployment does not set either variable.

## Tests

Run the repository tests from the project root:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests
```

Run the focused Financial Calm Brief and Build Week contract tests:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_financial_calm_brief.py \
  tests/test_ai_insights_service.py \
  tests/test_build_week_mode.py
```

These tests cover determinism, exactly-three output, fact-backed metrics, bilingual parity, AI validation, no-key fallback, Demo gating, and iPhone hiding.

## Product capabilities

- Income and expense tracking across months and currencies
- Dashboard summaries for balance, income, expenses, savings, and projects
- Projects, invoices, documents, recurring items, and tax-readiness workflows
- Cash-flow and financial-analysis services
- Arabic and English localization
- Optional Supabase cloud sync
- Local and cloud-safe persistence checks

## Project structure

```text
.
├── app.py
├── pages_floosy/        # Streamlit page modules
├── services/            # Finance, sync, tax, i18n, and analysis logic
├── models/              # Domain models
├── repositories/        # Data access layer
├── tests/               # Unit and service tests
├── e2e_tests/           # Browser smoke tests
└── README.md
```

## Privacy and safety

- Financial facts are computed locally by application code.
- Build Week data is synthetic and isolated from real users.
- API keys and Demo passwords stay outside Git.
- The competition feature is server-gated to one approved Demo identity.
- The AI layer cannot change numbers, decisions, or ordering.
