# Run HOLDOUT with a real model

The graph, evidence tools and release verifier work locally. The hosted Site displays a recorded execution. Configure model credentials in the local backend to run a fresh model-driven investigation.

## 1. Install the local project once

Extract the source archive into its own `holdout` directory. In a Linux/WSL terminal in that directory:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
python -m holdout doctor --install-native-module
```

The tested native installer is Linux x86-64. The local embedded FalkorDB process needs no cloud database key. Existing platform limitations are listed in `docs/validation.md`.

## 2. Add the model configuration

Make a local copy of `.env.example` called `.env` in the project root, beside `pyproject.toml`. Edit the copy in your own editor:

```dotenv
OPENAI_API_KEY='YOUR_REAL_API_KEY'
HOLDOUT_MODEL='gpt-6.1-sol'
HOLDOUT_API_BASE='https://api.openai.com/v1'
HOLDOUT_API_MODE='responses'
HOLDOUT_REASONING_EFFORT='low'
HOLDOUT_MAX_OUTPUT_TOKENS=4096
HOLDOUT_MAX_PROVIDER_CALLS=12
HOLDOUT_MAX_TOOL_CALLS=36
HOLDOUT_MAX_TOKENS=100000
HOLDOUT_MAX_SECONDS=180
```

Use an API key and model accessible to your API account. OpenAI keys are created at https://platform.openai.com/api-keys. API usage has billing separate from a ChatGPT subscription. Keep the key in the local file; do not paste it into the review page or into a chat. `.env` is excluded from Git and from the generated source archive.

Current GPT-6.1 Sol and GPT-6 Astra tool calls require the Responses API. The backend now supports it explicitly. For a different Chat Completions-compatible provider, change the base, model and key; set `HOLDOUT_API_MODE=chat_completions`. The reasoning setting applies only to Responses mode. The base URL ends at `/v1`; the backend appends the selected endpoint path.

## 3. Load the file and start

The Python CLI reads environment variables, so load your trusted `.env` explicitly in the same terminal:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m holdout serve
```

After editing `.env`, stop an existing server with Ctrl+C and run these loading/start commands again. Copying the file without loading it does not enable the agent.

Open **http://127.0.0.1:8000**. The status should say **Local backend connected**. Select **Run live agent**. If you previously investigated the run, use the reset button for a fresh comparison.

The live control appears when both the key and model are nonempty. This presence check does not establish that the account has credit or model access. The first request checks those in practice.

The execution record will show MCP tool actions and their evidence. A successful run ends with `agent.status = completed` and a verified conservative release. The provider call/tool count and token accounting are available under `agent.usage` in `/api/state`.

## 4. Capture a real run for review

After loading the same environment:

```bash
python -m holdout demo --mode llm --output .runtime/live-run.json
```

This starts a separate fresh run and writes an actual model-driven capture. It prints the path of the verified release directory. The captured run and manifest receipt contain no API key. Review the outcome before replacing the included deterministic capture or presenting it as a successful model evaluation.

## Configuration boundaries

- No separate MCP key or embedding key is needed for this implementation.
- No FalkorDB key is needed for the bundled local engine. A remote server instead uses `HOLDOUT_FALKORDB_URL` and its server credentials.
- `HOLDOUT_MAX_OUTPUT_TOKENS` includes reasoning tokens in Responses mode. An `incomplete` response stops before further tool actions; increase this limit deliberately if needed, within the overall budget.
- Call/token/time budgets stop continued work. They are not a guarantee of exact provider billing, and an in-flight native operation may finish.
- Provider HTTP 401 indicates an authentication failure. Other provider errors may reflect model access, account quota, endpoint compatibility or rate limits. HOLDOUT does not expose provider response bodies in the browser.
- The implementation has protocol tests for both API modes, including real MCP transport, but a real-provider success has not yet been recorded in this project because no key was available during development.

## Primary API references

- API keys: https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key
- Billing: https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform
- Model endpoint compatibility: https://developers.openai.com/api/docs/models/gpt-6.1-sol
- Function tools and `call_id`: https://developers.openai.com/api/docs/guides/function-calling
- Stateless reasoning: https://developers.openai.com/api/docs/guides/reasoning
