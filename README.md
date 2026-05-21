# Floosy

Floosy is a bilingual personal finance beta built with Streamlit. It helps users track money, projects, invoices, documents, savings, tax readiness, and optional cloud sync in one practical workflow.

The goal of this project is to turn everyday finance into a clearer system: less scattered data, more visibility, and better decisions.

## What it does

- Tracks income and expenses across months and currencies
- Shows dashboard summaries for balance, income, expenses, savings, and projects
- Organizes projects, invoices, documents, and recurring financial items
- Provides cash-flow and financial analysis services
- Supports Arabic and English copy through an i18n layer
- Supports optional Supabase cloud sync for beta users
- Includes beta testing, deployment, privacy, and feedback documentation

## Why this project matters

Floosy is more than a UI experiment. It includes product flows, data models, service logic, persistence, testing, deployment planning, and cloud-sync safety checks. It is built as a real beta product, not just a tutorial app.

## Tech stack

- Python
- Streamlit
- Pandas
- Supabase
- Pytest
- Playwright

## Project structure

```txt
.
├── app.py
├── pages_floosy/        # Streamlit page modules
├── services/            # Finance, sync, tax, i18n, and analysis logic
├── models/              # Domain models
├── repositories/        # Data access layer
├── tests/               # Unit and service tests
├── e2e_tests/           # Browser smoke tests
├── .streamlit/          # Streamlit config and secrets example
└── README.md
```

## Testing

This repository includes:

- 22 unit/service test files
- 1 Playwright smoke test
- Coverage for finance analysis, tax services, cloud sync guards, i18n, settings, local storage, invoices, and account workflows

Run tests with:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Local run

1. Create and activate a Python 3.12 environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add local secrets in `.streamlit/secrets.toml` if you want cloud sync.
4. Start the app:

```bash
streamlit run app.py
```

## Cloud sync

Cloud sync is optional. Local runs can use local persistence, while beta users can sign in and sync data through Supabase.

Required Streamlit secrets:

```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_ANON_KEY = "your-supabase-anon-key"
SUPABASE_DATA_TABLE = "user_app_data"
```

Do not commit real secrets to GitHub.

## Deployment

Recommended deployment target: Streamlit Community Cloud.

Deployment settings:

- App entrypoint: `app.py`
- Python runtime: `3.12.3`
- Secrets location: Streamlit app settings

Before deploying:

- Keep `.streamlit/secrets.toml` local only
- Add Supabase values in the deployment platform's secrets manager
- Test signup, signin, cloud sync, save, and reload flows

## Product docs

- [Beta test checklist](BETA_TEST_CHECKLIST.md)
- [Deployment readiness checklist](DEPLOYMENT_READINESS_CHECKLIST.md)
- [E2E testing guide](E2E_TESTING.md)
- [Privacy policy](PRIVACY_POLICY.md)
- [Beta feedback template](BETA_FEEDBACK_TEMPLATE.md)
- [App Store description](APP_STORE_DESCRIPTION.md)

## Status

Beta in progress.

Next improvements:

- Add screenshots or a short demo GIF
- Add a hosted demo link when deployment is ready
- Continue polishing onboarding and cloud-sync flows
- Package the strongest product story for the GitHub profile

