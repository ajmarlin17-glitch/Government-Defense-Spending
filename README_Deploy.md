# Deploying the web version (a link you can send to anyone)

This folder is a self-contained web app version of the desktop tool. It uses the exact same forecasting engine (`core.py` is a direct port of the model logic from the V7 desktop app), just with a browser front end (`app.py`, built with Streamlit) instead of a Tkinter window.

Once deployed, anyone you send the link to opens it in Chrome, Edge, or any browser on a normal Windows machine — nothing to install, no `.exe`, no security-software warnings.

## What's in this folder

- `app.py` — the web page itself (charts, controls, tables)
- `core.py` — the forecasting engine (data fetch, ensemble model, CBO blending, Monte Carlo bands) — no UI code
- `requirements.txt` — the three packages the app needs (Streamlit, Plotly, pandas)
- `Data/` — the same OMB and Treasury CSVs as the desktop app
- `Config/` — the same policy assumptions JSON as the desktop app (the API key file is deliberately **not** here — see the Congress.gov API key section below)

## Step 1: Test it on your own machine first (optional but recommended)

1. Install Python if you don't already have it (python.org).
2. Open a command prompt in this `WebApp` folder.
3. Run:
   ```
   pip install -r requirements.txt
   streamlit run app.py
   ```
4. It opens in your browser at `http://localhost:8501`. Confirm it looks right before sharing publicly.

## Step 2: Put this folder in a GitHub repository

Streamlit Community Cloud (the free hosting option) deploys directly from a GitHub repo. I can't create a GitHub account or push commits on your behalf, but the steps are quick:

1. Go to github.com and sign in (or create a free account).
2. Create a new repository — it can be private.
3. Upload this `WebApp` folder's contents to that repository (drag-and-drop upload works fine on github.com, or use `git push` if you're comfortable with git).
4. **Do not add `Config/GovernmentDefenceSpending_ApiKeys_V1.json`** to the repo — the `.gitignore` in this folder already excludes it, but double-check it isn't there before pushing, since anything in a public or shared repo is visible to anyone with access.

## Step 3: Deploy on Streamlit Community Cloud (free)

1. Go to share.streamlit.io and sign in with your GitHub account.
2. Click "New app," pick the repository you just created, and set the main file to `app.py`.
3. Click Deploy. It takes a minute or two the first time.
4. You'll get a URL like `https://your-app-name.streamlit.app` — that's the link to send people.

## Optional: enabling live congressional bill tracking

If you want the Congress.gov live bill-status feature to work on the deployed version, add your API key through Streamlit's secrets manager (not the JSON file, since that would be visible in the repo):

1. In the Streamlit Cloud dashboard, open your app's settings → Secrets.
2. Add:
   ```
   congress_api_key = "your-key-here"
   ```
3. Save. The app already checks `st.secrets` first (see `core.load_congress_api_key`), so no code changes are needed.

## Notes

- The app re-fetches Treasury data automatically (cached for 6 hours), same as the desktop app's refresh cycle.
- If Streamlit Cloud's outbound network can't reach the Treasury API, the app falls back to the bundled CSV snapshot in `Data/`, same fallback behavior as the desktop app.
- If you'd rather not use GitHub/Streamlit Cloud at all, this same `app.py`/`core.py` pair can be deployed to any host that runs Python (Render, Railway, an internal server, etc.) — the deployment steps just differ slightly by host.
