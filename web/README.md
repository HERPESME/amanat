# Amanat — interactive governed-core demo

A credential-free web demo for Cloud Run. A visitor sets a budget and a payee,
runs a settlement (or attacks it), and watches the policy engine allow or refuse
each action — then the signed evidence chain verifies itself in their browser.

Nothing here can move real money or spend an API quota: no LLM, no real payment
rail. It runs the same `AgentSession` / `PolicyEngine` / `EvidenceChain` the test
suite covers, on a settlement simulator.

## Run locally

```bash
uv run --with fastapi --with 'uvicorn[standard]' --with cryptography \
  uvicorn web.app:app --reload --port 8080
# open http://127.0.0.1:8080
```

## Deploy to Cloud Run (scale-to-zero)

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud run deploy amanat-demo \
  --source . --region asia-south1 \
  --allow-unauthenticated --port 8080 --min-instances 0
```

`--min-instances 0` idles the service to zero when no one is using it, so it
costs nothing between visits and cold-starts on the next request. The container
holds no secrets; the governed core needs none.
