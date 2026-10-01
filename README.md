# SQL Agent

**Ask what you want to know about your database in plain language and get a result table.**

For example: “Show revenue by month last year” or “Which products are ordered most often?”. The application finds the relevant tables, builds and runs a SQL query, and displays the result alongside the SQL used to obtain it. You can explore your data without writing each query yourself.

Upload a SQLite database and ask questions in the browser. Data processing and model inference run locally on your computer.

## How it works

The application imports your data into PostgreSQL and builds a searchable catalog of tables, columns and relationships. For each question it finds the relevant parts of that catalog, gives their structure to a local model, checks the generated SQL, and executes it with read-only credentials. The browser displays the resulting table or explains why the question could not be answered.

The [agent](src/agent/README.md) coordinates [retrieval](src/rag/README.md), [generation](src/llm/README.md), [SQL validation](src/sql/README.md) and [database access](src/database/README.md). These parts have separate responsibilities and share [domain types](src/domain/README.md), allowing each to evolve independently.

## Run

Install Python 3.11+ and Docker with Compose v2. Use Docker Desktop with Linux containers on Windows, or Docker Engine with Compose on Linux. Then run from this directory:

```text
python run.py
```

On Linux distributions without a `python` command, use `python3 run.py` with the same flags.

The command prepares the environment, downloads missing models, initializes the database and starts the application at **http://localhost:8501**. The first run needs internet access. Verified model files are reused on later runs.

```text
python run.py --list-models
python run.py --model qwen3.5-27b
python run.py --stop
```

The model catalog and application settings live in [config.yaml](config.yaml). Credentials are generated in `.runtime/compose.env`. Stopping the application preserves models and data.

See the [Russian guide](README_RU.md) for the full workflow, architecture, resource requirements and storage details. See [tests](tests/README.md), [validation status](docs/VALIDATION.md) and [feature ideas](docs/FEATURES.md) for development.
