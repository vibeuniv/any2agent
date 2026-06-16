# Example: Petstore

Turns the public Swagger Petstore API into an agent — zero vendor coupling.

## Live contract

```bash
any2agent init --openapi https://petstore3.swagger.io/api/v3/openapi.json \
             --project petstore \
             --base-url https://petstore3.swagger.io/api/v3
export OPENAI_API_KEY=sk-...          # any one provider key
any2agent serve --project petstore      # → http://127.0.0.1:8800
```

## Offline (no network)

A minimal local contract is included for offline trials:

```bash
any2agent init --openapi ./openapi-min.json --project petstore --base-url https://petstore3.swagger.io/api/v3
any2agent serve --project petstore
```

`petstore.toolspec.json` and `petstore.any2agent.toml` are generated in the working
directory (named after `--project`).
