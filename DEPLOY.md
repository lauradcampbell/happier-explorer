# Deploying 10% Happier Explorer to Railway

## What you need
- A GitHub account (free)
- A Railway account (free tier: 500 hours/month)

## Step-by-step

### 1. Create a GitHub repository

Go to https://github.com/new and create a new repository. Call it something
like `happier-chatbot`. Keep it **Private**. Don't add a README.

### 2. Push your files to GitHub

In Terminal, navigate to your project folder and run:

```bash
cd ~/happier-chatbot

# Initialize git
git init
git branch -M main

# Create a .gitignore so we don't upload unnecessary files
echo "transcripts/" > .gitignore
echo "episodes_index.json" >> .gitignore
echo "scrape_transcripts.py" >> .gitignore
echo "chunk_transcripts.py" >> .gitignore
echo "__pycache__/" >> .gitignore

# Add the files we need for deployment
git add chatbot_cloud.py chunks.json requirements.txt Procfile .gitignore
git commit -m "Initial deploy"

# Connect to your GitHub repo (replace YOUR_USERNAME with your GitHub username)
git remote add origin https://github.com/YOUR_USERNAME/happier-chatbot.git
git push -u origin main
```

Note: chunks.json is about 20MB. GitHub allows files up to 100MB so this is fine.

### 3. Deploy on Railway

1. Go to https://railway.app/ and sign up with your GitHub account
2. Click "New Project" → "Deploy from GitHub Repo"
3. Select your `happier-chatbot` repository
4. Railway will detect the Procfile and start building

### 4. Set environment variables

In your Railway project dashboard:
1. Click on your service
2. Go to the "Variables" tab
3. Add these three variables:
   - `ANTHROPIC_API_KEY` = your API key (sk-ant-...)
   - `CHATBOT_PASSWORD` = choose a password you'll remember
   - `DAILY_LIMIT` = 50 (or whatever you want)

### 5. Generate a public URL

1. In Railway, go to your service's "Settings" tab
2. Under "Networking", click "Generate Domain"
3. Railway will give you a URL like `happier-chatbot-production-xxxx.up.railway.app`

### 6. Open it!

Visit that URL on your phone or any browser. Enter your password and start asking questions.

## Updating later

If you scrape more episodes and rebuild chunks.json, just push the update:

```bash
cd ~/happier-chatbot
git add chunks.json
git commit -m "Updated transcripts"
git push
```

Railway will automatically redeploy.

## Cost

- Railway free tier: $0 (500 hours/month is plenty)
- Anthropic API: ~$0.01-0.02 per question
