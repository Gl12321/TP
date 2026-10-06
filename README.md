# Razbor

**Ask for the data you need in plain language, get a table from your database, and investigate the result with your team.**

Razbor is an analytics workspace for businesses with multiple stores, locations or franchisees. Managers compare actual performance, targets and previous periods; analysts define consistent metrics; location managers answer questions about specific observations. A local SQL assistant handles questions that go beyond the standard overview and returns a table together with the query behind it.

For example, a manager notices a sales decline, asks for a breakdown by location, checks the generated SQL and saves the result. A case links that evidence to an assigned question, a colleague's answer and an explicit conclusion. The original numbers remain available when a report is refreshed or a standard metric is measured again.

The main documentation is in Russian: [README_RU.md](README_RU.md). The interface is also in Russian.

## What the product provides

- Network, city and location analytics with actuals, monthly targets, comparable periods and daily/weekly trends.
- Private natural-language conversations with tables, SQL, execution details, charts and CSV export.
- Saved reports with immutable original results and a separate refresh history.
- Cases with evidence, comments, assigned questions, answers, conclusions and repeated metric measurements.
- Technical source issues with assignees, discussion and resolution history.
- Workspaces, invitations, role permissions, location scopes and explicit source audiences.

Permissions are checked by the server, including when reading saved results. Technical administrators can maintain sources without financial access. AI is used for SQL generation; metric calculations, plans, permissions, discussions and notifications are implemented as application logic.

See the [product guide](docs/PRODUCT.md) for the complete implemented feature set, role boundaries and collaboration scenarios.

## Architecture

| Component | Responsibility |
| --- | --- |
| [Frontend](frontend/README.md) | React and TypeScript pages, forms, tables, SQL, charts and collaboration UI |
| [Backend](backend/README.md) | Python/FastAPI sessions, permissions, sources, analytics, history, collaboration and persistent jobs |
| [SQL agent](packages/sql_agent/README.md) | Standalone Python library for catalog retrieval, prompts, constrained generation and SQL validation |

The application's PostgreSQL stores workspaces and application state. Business PostgreSQL databases are connected separately as read-only sources. The API serves both HTTP endpoints and the built frontend; a separate worker executes queued requests using `sql_agent`. Closing a browser tab does not cancel or discard a server-side job.

The model receives the allowed schema context. Generated SQL is validated, and the backend applies permitted columns and location scope before execution. Business metric meaning still depends on agreed definitions and correctly prepared source data. The [architecture guide](docs/ARCHITECTURE.md) describes the boundaries and data flow.

## Prepare the machine and run

Requirements: **Python 3.11+** and a running **Docker with Compose v2**. On Windows, use Docker Desktop with Linux containers; on Linux, use Docker Engine and Compose. Node.js and application Python dependencies are installed inside the images for this deployment method.

Start with the [installation guide](docs/operations/install.md) if these tools are missing. It includes Windows and Ubuntu installation commands, WSL setup, restarts, permissions and readiness checks. The launcher manages the application after these prerequisites are installed; it does not install Python or Docker on the host.

From the repository root on Windows, after installing Python Launcher and starting Docker Desktop:

```powershell
py -3.12 run.py --check
py -3.12 run.py --model qwen3.5-9b
```

On Linux, after installing Python and starting Docker Engine:

```bash
python3 run.py --check
python3 run.py --model qwen3.5-9b
```

Continue after a successful `--check`, which confirms a running Engine using Linux containers. A `python` command that opens Microsoft Store is an alias, not a working interpreter. Private `.tools/python` installations exist only in prepared working copies and are not included in a clone.

Open `http://localhost:8000` after the launcher prints the address. Use the printed initial setup code to create the first account. Existing installations retain their accounts and data. The host check does not build images or verify model generation; these happen during the real launch.

The launcher builds the required images, prepares the database, applies migrations and starts the API and worker. Prepared model files are reused; missing files for the selected model and retrieval models are downloaded automatically. An uncached first run requires network access, disk space for images and weights, and enough memory for the selected profile.

Available profiles are `qwen3.5-4b`, `qwen3.5-9b` and `qwen3.5-27b`. The catalog is in [config.yaml](config.yaml). Model file size is not total application memory usage.

Use the same interpreter with `run.py --list-models`, `run.py --without-ai` or `run.py --stop`. Listing models also needs Docker and may build the preparation image. `run.py --check` performs no installation, builds or downloads.

`--without-ai` starts the UI and API without downloading model weights or starting the worker. Setup, standard analytics, discussions and saved results remain available; new assistant requests and SQL report refreshes require the worker. `--stop` preserves databases, models and secrets.

A new workspace needs locations, a source and a metric definition. Before connecting a business database, add its hostname to `application.source_hosts` in the YAML configuration; the default empty list allows no source hosts.

## Product demo

The demo opens a sample network with six roles, financial data and collaboration examples. The `onboarding` scenario provides a separate workspace for exploring initial setup. Each scenario has its own databases and retains user changes; automated checks do not use these data.

This native mode requires Python, `requirements.txt`, a built frontend and a **running dedicated PostgreSQL server**. Node.js is needed to build the frontend. `RAZBOR_DEMO_DATABASE_URL` supplies an administrative connection for initial preparation; PostgreSQL must remain available for subsequent launches. The demo does not start PostgreSQL, a model or a worker.

Follow [demo setup from scratch](docs/demo/README.md#подготовка-demo-с-нуля), then on Windows:

```powershell
.\.venv\Scripts\python.exe -m tools.demo --check
.\.venv\Scripts\python.exe -m tools.demo
```

On Linux, use `.venv/bin/python` with the same arguments. See the [demo guide](docs/demo/README.md) for accounts, ports and walkthroughs.

## Automated checks

First prepare a Python environment using `requirements-dev.txt` and install frontend dependencies with `npm ci`, following [development setup](docs/development/README.md#2-окружение-windows-и-linux). Then run `.\.venv\Scripts\python.exe tools/check.py quick` on Windows or `.venv/bin/python tools/check.py quick` on Linux. This runs compact Python and frontend contract checks plus TypeScript without a PostgreSQL server or model weights. The suite focuses on permissions, SQL, financial precision and safe retries; it does not cover every screen or assess real model quality.

PostgreSQL and browser checks run separately against their own environments and data. See the [testing guide](docs/development/testing.md) for Windows/Linux commands, dependencies and interpretation of results.

## Documentation

| Topic | Guide |
| --- | --- |
| Product functionality and roles | [Product](docs/PRODUCT.md) |
| Components and their responsibilities | [Architecture](docs/ARCHITECTURE.md) |
| Code changes and extension points | [Development](docs/development/README.md): [backend](docs/development/backend.md), [SQL agent](docs/development/sql-agent.md), [frontend](docs/development/frontend.md) |
| Startup, dependencies and source setup | [Operations](docs/operations/README.md) |
| Install prerequisites on a new machine | [Installation: Windows and Linux](docs/operations/install.md) |
| Containers, volumes, updates and deployment | [Docker](docs/operations/docker.md) |
| Models, limits and secrets | [Configuration](docs/operations/configuration.md) |
| Backup and restore | [Backups](docs/operations/backup.md) |
| Browser/API contract | [API](backend/API.md) |
| Explore the interface, sample data and roles | [Product demo](docs/demo/README.md) |
| Check code changes automatically | [Automated checks](docs/development/testing.md) |
