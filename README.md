# Plate Complaint PoC

A small Streamlit proof of concept for filing vehicle complaints from a photo of a
license plate. The operator captures or uploads a photo, the plate is read by the
Plate Recognizer cloud API, the operator confirms it, and the app looks the plate up
in a local CSV of residents and builds a complaint draft.

## Contents

- [Prerequisites](#prerequisites)
- [Local development](#local-development)
- [Owner CSV format](#owner-csv-format)
- [Docker deployment (HTTPS via Caddy)](#docker-deployment-https-via-caddy)
- [Streamlit Community Cloud](#streamlit-community-cloud)
- [Mobile access](#mobile-access)
- [Environment variables](#environment-variables)
- [Troubleshooting](#troubleshooting)
- [PoC limitations](#poc-limitations)

## Prerequisites

- Python 3.12
- Docker and the Docker Compose plugin (for the containerized deployment)
- A Plate Recognizer account and API token
- For the Docker deployment only: a domain name whose DNS you control

## Local development

```bash
cp .env.example .env      # then edit .env and set PLATERECOGNIZER_TOKEN
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps plus pytest
streamlit run app.py
```

Open <http://localhost:8501>.

`localhost` counts as a secure context, so the camera works locally without HTTPS.
Note that `data/owners.csv` must exist before the lookup will return anything.

## Owner CSV format

`data/owners.csv` has exactly this header, one row per resident:

```csv
plate,unit_no
51F-123.45,A-101
51F98765,B-204
```

Matching is done on a normalized form of the plate: uppercased and stripped of every
separator, so `51F-123.45`, `51f 12345` and `51F12345` are all the same plate.
Commas are the field separator; do not put commas inside a value.

## Docker deployment (HTTPS via Caddy)

1. Create a DNS **A record** for your domain pointing at the host's public IP and wait
   for it to resolve (`dig +short $DOMAIN`).
2. Create the environment file and fill in the real token and domain:

   ```bash
   cp .env.example .env
   ```

3. Start the stack:

   ```bash
   docker compose up -d
   ```

4. Caddy provisions the TLS certificate automatically on first request. Watch it
   finish:

   ```bash
   docker compose logs -f caddy
   ```

5. Open `https://$DOMAIN`.

The `app` container is never published to the host; Caddy is the only public entry
point and forwards requests to `app:8501`. `./data` is bind-mounted into the container
so `owners.csv` and any generated output survive restarts and rebuilds.

## Streamlit Community Cloud

No code changes are needed for Community Cloud: the app never writes to disk, needs no
system packages, and Cloud's HTTPS satisfies the browser camera requirement.

1. Push this repository to GitHub. `.env` and `.streamlit/secrets.toml` are
   gitignored. `data/owners.csv` **must** be committed, because the app reads it from
   the checkout at startup.
2. On <https://share.streamlit.io> click **Create app** and point it at the
   repository, branch and `app.py`.
3. In **Advanced settings**, select Python 3.12 (the default) and paste the app's
   secrets:

   ```toml
   PLATERECOGNIZER_TOKEN = "your-token-here"
   PLATERECOGNIZER_TEST_MODE = "0"
   ```

4. Deploy. Secrets can be edited later under the app's settings; saving restarts the
   app.

Settings are resolved from Streamlit secrets first and fall back to environment
variables, so the same code serves Cloud (secrets) and local/Docker (`.env`) runs. A
local `.streamlit/secrets.toml`, if you create one, overrides `.env`.

Differences from the Docker deployment:

- The app is public at `https://<name>.streamlit.app` unless you restrict viewers in
  the app's sharing settings. Every recognition spends Plate Recognizer credits, so
  set a spend limit in the Plate Recognizer dashboard for a public demo.
- The filesystem is ephemeral and apps sleep after 12 hours without traffic. Session
  state (complaint drafts) and any future file output disappear on reboot; commit
  `owners.csv` and redeploy to change it, or move the data to a database.
- `Dockerfile`, `docker-compose.yml`, `Caddyfile` and `DOMAIN` are unused on Cloud;
  the platform supplies HTTPS and its own hostname.

## Mobile access

`streamlit.camera_input` uses the browser's `getUserMedia`, which browsers only expose
in a **secure context** (`https://…` or `localhost`). Opening the app on a phone at
`http://192.168.x.x:8501` will *not* show a working camera, even on the same Wi-Fi.

Test on a phone by navigating to the HTTPS domain from the DNS setup above, and
grant the browser camera permission when prompted. If the camera still does not open,
use the file-upload input to pick a photo from the gallery instead; that path works
over HTTPS without any camera permission.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `PLATERECOGNIZER_TOKEN` | yes | Plate Recognizer API token. Server-side only; never commit it. |
| `PLATERECOGNIZER_TEST_MODE` | no | `1` uses the bundled fixture instead of the live API. Default `0`. |
| `PLATERECOGNIZER_FIXTURE` | no | Path to a different fixture JSON. Only used when test mode is on. |
| `DOMAIN` | Docker only | Public hostname Caddy terminates HTTPS for, e.g. `plates.example.com`. |

Set these in `.env` for local and Docker runs, or in the app's secrets on Streamlit
Community Cloud. Secrets take precedence over the environment. Test mode reads
`tests/fixtures/platerecognizer_response.json`; that directory is excluded from the
Docker image, so use test mode for local demos rather than inside the container.

## Troubleshooting

- **401 from the OCR API** — the token is wrong, expired, or has stray whitespace or
  quotes in `.env` (or in the app's secrets on Cloud). Re-copy the token from the Plate
  Recognizer dashboard.
- **403 or 429 from the OCR API** — the account quota is exhausted or requests are
  being throttled. The app must be rate-limited by a human; for a demo, set
  `PLATERECOGNIZER_TEST_MODE=1` to use the offline fixture.
- **Camera does not open** — the page is not on HTTPS (or `localhost`), or the browser
  blocked the camera. Check the address bar and the site permissions, then reload. Use
  the upload fallback if needed.
- **Plate not found** — confirm the row exists in `data/owners.csv` and that the header
  is exactly `plate,unit_no`. Remember matching is on the normalized plate
  (uppercase, separators removed), so `51F-123.45` matches `51F12345`.
- **Caddy cannot get a certificate** — the DNS A record does not point at this host
  yet, ports 80/443 are blocked or in use, or the domain is behind a CDN proxy that
  intercepts the ACME challenge. `docker compose logs caddy` shows the ACME error.
- **App container is unhealthy** — check `docker compose logs app`; the healthcheck
  polls `/_stcore/health` on port 8501.
- **"No Plate Recognizer token configured" on Community Cloud** — the secret key must
  be exactly `PLATERECOGNIZER_TOKEN` in the app's secrets (TOML, plain
  `KEY = "value"`, no section header). Saving secrets restarts the app.
- **Changes do not appear after editing code** — rebuild: `docker compose up -d --build`.

## PoC limitations

This is a demonstration, not a production system:

- **No authentication.** Anyone who can reach the URL can file complaints and view
  resident plate/unit data. On Docker, keep it behind Caddy on a trusted network; on
  Community Cloud, restrict viewers in the app's sharing settings.
- **CORS is relaxed for the Docker stack.** The `app` service starts Streamlit with
  `--server.enableCORS=false` because Caddy is the only proxy, which means Streamlit
  accepts cross-origin WebSocket connections. Community Cloud supplies its own CORS
  and XSRF settings and ignores this flag. When you add authentication to the Docker
  deployment, drop the flag and set `enableCORS = true` with `corsAllowedOrigins`
  (or `server.allowedHosts`) set to the real hostname.
- **No persistence of complaints.** Drafts are shown in the app session only and are
  lost on refresh or when the process restarts.
- **No image or evidence retention.** Photos are processed for OCR and discarded; there
  is no audit trail to attach to a real complaint.
- **CSV, not a database.** `data/owners.csv` is hand-edited and loaded on demand: no
  concurrency control, no history, no validation beyond the two columns.
