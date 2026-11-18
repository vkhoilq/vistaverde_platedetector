# Quickstart

Fast path to run the app on this machine. For full docs (deployment, CSV format,
troubleshooting) see [README.md](README.md).

## Run it

```bash
cd /Volumes/AData/Work/vistaverde_platedetector
source .venv/bin/activate
streamlit run app.py
```

Open **http://localhost:8501**

The venv, `.env` (API token) and `data/owners.csv` are already set up.

## Quick test

1. Upload `sample_plate.jpg` (or use the camera on desktop).
2. Click **Recognise plate** — this is a live API call and costs one credit.
3. You should land on **2. Confirm** with plate `51F-123.45`, confidence ~97%.
4. Click **Search owner** → **Found — Unit B-12**.
5. Click **Generate complaint** → the draft card, copyable as text.

Failure paths worth trying:

- Edit the plate to `51F-999.99` → **Search owner** → *Plate not found* plus a
  "Did you mean? 51F-999.11 — Unit C-15" suggestion.
- Press **Back to photo** → returns to capture with a "Plate detected" summary and
  a **Continue to confirm** button.

Plates in the sample CSV: `51F-123.45`→B-12 · `30A-123.45`→A-03 ·
`51H-999.88`→Tower 1 - 09 · `59H1-234.56`→Tower 2 - 05

## On a phone

Same Wi-Fi: **http://192.168.100.28:8501** (check the IP — it may change).

Use **Upload photo**, not the camera. Browsers only allow camera access over
`https://` or `localhost`, so the camera needs the Caddy + HTTPS setup (see README).

## Fresh machine

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then set PLATERECOGNIZER_TOKEN
streamlit run app.py
```

Deploying to Streamlit Community Cloud instead? See
[README.md](README.md#streamlit-community-cloud).

## Notes

- Mode is controlled by `PLATERECOGNIZER_TEST_MODE` in `.env`. `0` = live API,
  `1` = bundled fixture (no network, no credits — good for demos).
- Each **Recognise plate** click bills one Plate Recognizer call. It is not
  re-called on page reruns for the same photo.
- No login. Anyone who can reach the URL can file complaints, so keep it on
  localhost or a trusted LAN.
- Backend tests: `python -m pytest tests/ -q` (no network or token needed).
