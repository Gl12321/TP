# Razbor

**Ask a business question in plain language, get a table from your database, and work through the result with your team.**

Razbor is an analytics workspace for retail and franchise networks. Leaders compare stores and periods, analysts define shared metrics, and local managers respond to questions about their operations. A result can become a saved report or a collaborative investigation with an assignee, directed questions, comments and a recorded conclusion.

The AI component turns questions into SQL. Accounts, permissions, dashboards, plans, discussions and notifications use ordinary application code. Models run locally; the application connects to a customer's PostgreSQL database through a restricted read-only connection.

The complete product and architecture guide is available in [Russian](README_RU.md).

## From a question to a decision

A leader notices a change in the overview and asks which stores contributed to it. The assistant returns a table with an optional chart and the executed SQL available in an expandable section. The leader attaches that snapshot to an investigation and asks a store manager a specific question. The manager receives a notification, responds, and the team records its conclusion against the same calculation. A later measurement can check the effect of the agreed action while preserving the original evidence. Saved reports retain their refresh history.

Different roles share a consistent interface while seeing their assigned store scope and permitted actions. Source profiles separately restrict readers, tables and columns, so access to sales need not expose a franchisee's expenses. Administrators can manage connections without automatically gaining access to financial data. Every request and saved result is checked against current server-side permissions.

## Architecture

- [React frontend](frontend/README.md): overview, stores, assistant, reports, investigations, participants and data setup.
- [Python backend](backend/README.md): FastAPI, account sessions, access rules, analytics and a persistent job queue in the application PostgreSQL database.
- [Standalone SQL agent](packages/sql_agent/README.md): catalog retrieval, local generation, grammar and AST validation behind explicit Python interfaces.
- [Runtime preparation](runtime/README.md): pinned model downloads, integrity checks and database setup.

A separate worker runs one AI task at a time. The API remains responsive during generation. SQL execution projects permitted columns and binds the user's allowed store codes before reading source data. The application database stores workflow state and immutable results; customer data stays in the connected source.

## Run

Install Python 3.11+ and Docker with Compose v2. On Windows use Docker Desktop with Linux containers; on Linux use Docker Engine and Compose.

```text
python run.py
```

Open **http://localhost:8000** and use the setup code printed by the launcher to create the first account. Subsequent members join through invitations. On Linux systems without a `python` command, use `python3`.

```text
python run.py --list-models
python run.py --model qwen3.5-4b
python run.py --model qwen3.5-9b
python run.py --model qwen3.5-27b
python run.py --without-ai
python run.py --stop
```

The default model is Qwen3.5 9B. Missing model files are downloaded and verified; existing verified files are reused. `--without-ai` starts the application without downloading or loading models. Stopping preserves data, weights and credentials.

Settings are in [config.yaml](config.yaml); generated secrets are in the Git-ignored `.runtime/compose.env`. Source hosts must be explicitly allowed before connecting a database. See [operations](docs/OPERATIONS.md) for setup, storage, HTTPS and recovery.

## Verification and limits

See [validation status](docs/VALIDATION.md) for actual test runs and the remaining integration work. The current environment has no Docker; Docker execution, Linux execution and inference with real model weights have not been verified here. A configured 27B profile is not a guarantee that it fits a 24 GB laptop.

The implemented application is documented beside its code. [Research](docs/research/README.md) and [prototypes](docs/prototypes/README.md) preserve earlier design decisions and may describe capabilities beyond the implementation.
