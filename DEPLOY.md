# Deploying 10% Happier Explorer to Northflank

This replaces the old Railway setup. Railway used the `Procfile`; Northflank
builds the `Dockerfile` in this repo instead.

## How the two repos fit together

| Repo | What's in it | Role |
|---|---|---|
| `lauradcampbell/happier-explorer` (public) | code only — transcripts and `chunks*.json` are gitignored | source of truth |
| `lauradcampbell/happier-chatbot` (private) | `chatbot_cloud.py`, `requirements.txt`, `Dockerfile`, `chunks_part1.json`, `chunks_part2.json` | what the host builds |

The transcript data isn't ours to republish, so it lives only in the private
repo. **Northflank deploys from the private `happier-chatbot` repo.**

Because the code exists in both places it can drift — that's how the deployed
copy ended up running a retired model id. After changing `chatbot_cloud.py`,
push it to both.

## One-time setup

### 1. Sync the private deploy repo

From the public repo checkout:

```bash
git clone https://github.com/lauradcampbell/happier-chatbot.git /tmp/deploy
cp chatbot_cloud.py requirements.txt Dockerfile /tmp/deploy/
cd /tmp/deploy && git add -A && git commit -m "Move to Northflank" && git push
```

### 2. Create the Northflank service

1. Sign up at https://northflank.com and connect your GitHub account,
   granting access to the private `happier-chatbot` repo.
2. **Create new → Service → Combined service** (combined = build + deploy from
   one branch, with CI/CD on by default).
3. Repository: `happier-chatbot`, branch `main`.
4. Build type: **Dockerfile**, path `/Dockerfile`, context `/`.
5. Resources: **at least 512 MB memory**. The app loads ~28 MB of JSON and
   builds a TF-IDF index over 10,384 chunks in memory — measured peak is about
   250 MB, so 256 MB will be OOM-killed. 1 GB is comfortable.
6. Instances: **1**. Logins and the daily rate limit are held in memory, so a
   second instance would give inconsistent limits and random logouts.

### 3. Environment variables

Under the service's **Environment** (runtime variables), add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key (`sk-ant-...`) |
| `CHATBOT_PASSWORD` | the password you'll type to get in |
| `DAILY_LIMIT` | `50` (or whatever cap you want) |

Don't set `PORT` — the Dockerfile sets it to 8080.

### 4. Ports and public URL

Northflank picks up `EXPOSE 8080` from the Dockerfile. On the service's
**Ports & DNS** page, make sure port `8080` is set to **public** with HTTP
protocol. Northflank issues the TLS certificate and gives you a
`*.code.run` URL.

### 5. Health check (optional but recommended)

Add an HTTP health check on path `/`, port 8080. Give it a **startup grace
period of at least 60 seconds** — the search index takes a while to build
before the server starts answering.

### 6. Verify, then retire Railway

Open the Northflank URL, log in, ask a question, confirm you get an answer
with episode citations. Leave the Railway deployment running until that works,
then delete the Railway project so it stops consuming hours.

## Updating later

Code change: edit `chatbot_cloud.py` here, commit, then copy it into the
private repo and push there too. Northflank rebuilds automatically.

New transcripts: rerun `scrape_transcripts.py` and `chunk_transcripts.py`,
then push the regenerated `chunks_part1.json` / `chunks_part2.json` to the
private repo.

Restarting the service logs everyone out (sessions are in memory) and resets
the daily counter. That's expected.

## Cost

- Northflank: consumption-based. The free Sandbox tier covers 2 services;
  beyond that, roughly $0.017/vCPU-hour plus $0.008/GB-hour for what you
  allocate.
- Anthropic API: ~$0.01–0.02 per question.

## Leftover from Railway

`Procfile` is unused now — Northflank builds the `Dockerfile`. It's kept only
in case you ever want to redeploy on a Procfile-based host.
