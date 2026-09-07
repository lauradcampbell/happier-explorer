#!/usr/bin/env python3
"""
10% Happier Podcast Chatbot — Cloud-Ready Web App

A web app that lets you ask questions about the 10% Happier podcast.
It searches through transcript chunks using TF-IDF keyword matching,
then uses Claude to generate a conversational answer grounded in the results.

Features:
    - Password protection (set via CHATBOT_PASSWORD env var)
    - Daily rate limit (set via DAILY_LIMIT env var, default 50)
    - Works locally or deployed to Railway/Render/Fly.io

Usage (local):
    export ANTHROPIC_API_KEY="sk-ant-..."
    export CHATBOT_PASSWORD="your-password"
    python3 chatbot.py
    Open http://localhost:8000

Usage (cloud):
    Set ANTHROPIC_API_KEY, CHATBOT_PASSWORD, and optionally
    DAILY_LIMIT and PORT as environment variables in your cloud dashboard.
"""

import json
import math
import os
import re
import string
import hashlib
import secrets
import time
from collections import Counter
from http.server import HTTPServer, SimpleHTTPRequestHandler
import anthropic

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8000))
CHUNKS_FILE = "chunks.json"
TOP_K = 8
MODEL = "claude-sonnet-4-20250514"
PASSWORD = os.environ.get("CHATBOT_PASSWORD", "")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 50))


# --- Rate Limiter ---

class RateLimiter:
    def __init__(self, daily_limit: int):
        self.daily_limit = daily_limit
        self.requests = {}  # token -> list of timestamps
        self._cleanup_interval = 3600  # cleanup old entries every hour
        self._last_cleanup = time.time()

    def check(self, token: str) -> tuple[bool, int]:
        """Returns (allowed, remaining) for the given token."""
        self._maybe_cleanup()
        now = time.time()
        today_start = now - (now % 86400)  # start of UTC day

        if token not in self.requests:
            self.requests[token] = []

        # Filter to today's requests only
        self.requests[token] = [t for t in self.requests[token] if t >= today_start]
        count = len(self.requests[token])
        remaining = max(0, self.daily_limit - count)

        if count >= self.daily_limit:
            return False, 0

        self.requests[token].append(now)
        return True, remaining - 1

    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup > self._cleanup_interval:
            yesterday = now - 86400
            for token in list(self.requests.keys()):
                self.requests[token] = [t for t in self.requests[token] if t >= yesterday]
                if not self.requests[token]:
                    del self.requests[token]
            self._last_cleanup = now


# --- Session Management ---

class SessionManager:
    """Simple token-based sessions. No cookies, no database."""
    def __init__(self):
        self.valid_tokens = set()

    def create_token(self) -> str:
        token = secrets.token_hex(32)
        self.valid_tokens.add(token)
        return token

    def is_valid(self, token: str) -> bool:
        return token in self.valid_tokens


# --- TF-IDF Search Engine ---

class SearchEngine:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.stop_words = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "is", "it", "that", "this", "was", "are",
            "be", "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "not", "no", "so", "if",
            "then", "than", "too", "very", "just", "about", "up", "out", "all",
            "also", "as", "into", "more", "some", "what", "when", "where", "which",
            "who", "how", "been", "being", "were", "he", "she", "they", "we", "you",
            "i", "me", "my", "your", "his", "her", "its", "our", "their", "them",
            "him", "us", "am", "going", "get", "got", "like", "know", "think",
            "say", "said", "one", "two", "well", "really", "right", "because",
            "don", "re", "ve", "ll", "yeah", "okay", "oh", "um", "uh",
            "dan", "harris", "percent", "happier", "podcast", "episode", "show",
        }
        self._build_index()

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = text.translate(str.maketrans("", "", string.punctuation))
        tokens = text.split()
        return [t for t in tokens if t not in self.stop_words and len(t) > 2]

    def _build_index(self):
        print("  Building search index...")
        self.doc_tokens = []
        self.df = Counter()

        for chunk in self.chunks:
            tokens = self._tokenize(chunk["text"])
            self.doc_tokens.append(tokens)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                self.df[t] += 1

        self.n_docs = len(self.chunks)
        print(f"  Index built: {self.n_docs} chunks, {len(self.df)} unique terms")

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = []
        for i, doc_tokens in enumerate(self.doc_tokens):
            if not doc_tokens:
                scores.append(0.0)
                continue

            tf = Counter(doc_tokens)
            doc_len = len(doc_tokens)
            score = 0.0

            for qt in query_tokens:
                if qt in tf:
                    term_freq = tf[qt] / doc_len
                    idf = math.log((self.n_docs + 1) / (self.df.get(qt, 0) + 1))
                    score += term_freq * idf

            scores.append(score)

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = []
        for idx in ranked[:top_k]:
            if scores[idx] > 0:
                result = dict(self.chunks[idx])
                result["score"] = round(scores[idx], 4)
                results.append(result)

        return results


# --- Claude Answer Generation ---

def generate_answer(client: anthropic.Anthropic, query: str, results: list[dict]) -> str:
    if not results:
        return "I couldn't find any relevant passages in the podcast transcripts for that question. Try rephrasing or using different keywords."

    context_parts = []
    for i, r in enumerate(results):
        context_parts.append(
            f"--- Episode: {r['episode_title']}\n"
            f"    Date: {r['episode_date']}\n"
            f"    Guest: {r.get('guest', 'N/A')}\n"
            f"    Timestamps: {r['timestamp_start']} - {r['timestamp_end']}\n"
            f"    Passage:\n{r['text']}\n"
        )

    context = "\n".join(context_parts)

    system_prompt = """You are a helpful assistant that answers questions about the 10% Happier podcast with Dan Harris. 
You answer based ONLY on the transcript passages provided below. 

Guidelines:
- Ground your answers in specific things said in the transcripts
- Mention the episode title and guest when referencing specific content
- If multiple episodes cover the topic, synthesize across them
- If the passages don't really answer the question, say so honestly
- Be conversational and warm, matching the vibe of the podcast
- When relevant, note the episode so the user can go listen to it
- Keep answers concise but substantive — aim for 2-4 paragraphs"""

    user_message = f"""Based on these transcript passages from the 10% Happier podcast, please answer this question:

**Question:** {query}

**Relevant transcript passages:**

{context}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


# --- HTML Templates ---

LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>10% Happier Explorer</title>
    <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=DM+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #faf8f4;
            --bg-chat: #ffffff;
            --text: #2c2416;
            --text-muted: #8a7e6e;
            --accent: #c4713b;
            --border: #e5dfd5;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'DM Sans', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-box {
            text-align: center;
            max-width: 360px;
            padding: 40px;
        }
        h1 {
            font-family: 'Instrument Serif', Georgia, serif;
            font-size: 32px;
            font-weight: 400;
            margin-bottom: 8px;
        }
        .subtitle {
            font-size: 14px;
            color: var(--text-muted);
            margin-bottom: 32px;
        }
        input {
            width: 100%;
            padding: 14px 20px;
            border: 1px solid var(--border);
            border-radius: 12px;
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            background: var(--bg-chat);
            color: var(--text);
            outline: none;
            margin-bottom: 12px;
        }
        input:focus { border-color: var(--accent); }
        button {
            width: 100%;
            padding: 14px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            font-weight: 500;
            cursor: pointer;
        }
        button:hover { background: #b3622f; }
        .error-msg {
            color: #991b1b;
            font-size: 13px;
            margin-top: 12px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>10% Happier Explorer</h1>
        <p class="subtitle">Enter the password to start exploring</p>
        <input type="password" id="pw" placeholder="Password" onkeydown="if(event.key==='Enter')login()" autofocus>
        <button onclick="login()">Enter</button>
        <p class="error-msg" id="err">Incorrect password. Try again.</p>
    </div>
    <script>
        async function login() {
            const pw = document.getElementById('pw').value;
            const resp = await fetch('/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({password: pw})
            });
            const data = await resp.json();
            if (data.token) {
                localStorage.setItem('chatbot_token', data.token);
                window.location.reload();
            } else {
                document.getElementById('err').style.display = 'block';
            }
        }
    </script>
</body>
</html>"""

CHAT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>10% Happier — Podcast Explorer</title>
    <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=DM+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #faf8f4;
            --bg-chat: #ffffff;
            --bg-user: #e8e2d8;
            --bg-bot: #f5f2ec;
            --text: #2c2416;
            --text-muted: #8a7e6e;
            --accent: #c4713b;
            --accent-light: #f0ddd0;
            --border: #e5dfd5;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DM Sans', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            padding: 20px 24px 16px;
            border-bottom: 1px solid var(--border);
            background: var(--bg);
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }

        header h1 {
            font-family: 'Instrument Serif', Georgia, serif;
            font-size: 24px;
            font-weight: 400;
            color: var(--text);
        }

        .header-right {
            font-size: 12px;
            color: var(--text-muted);
        }

        .remaining {
            background: var(--accent-light);
            color: var(--accent);
            padding: 3px 10px;
            border-radius: 10px;
            font-weight: 500;
            font-size: 11px;
        }

        .chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 20px 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .welcome {
            text-align: center;
            padding: 40px 16px;
            color: var(--text-muted);
        }

        .welcome h2 {
            font-family: 'Instrument Serif', Georgia, serif;
            font-size: 20px;
            font-weight: 400;
            color: var(--text);
            margin-bottom: 10px;
        }

        .welcome p {
            font-size: 14px;
            line-height: 1.6;
            max-width: 480px;
            margin: 0 auto;
        }

        .suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
            margin-top: 16px;
        }

        .suggestion {
            background: var(--bg-chat);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 8px 14px;
            font-size: 13px;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s;
        }

        .suggestion:hover {
            border-color: var(--accent);
            color: var(--accent);
            background: var(--accent-light);
        }

        .message {
            max-width: 720px;
            width: 100%;
            margin: 0 auto;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message.user {
            background: var(--bg-user);
            padding: 12px 18px;
            border-radius: 16px 16px 4px 16px;
            font-size: 15px;
            line-height: 1.5;
            align-self: flex-end;
            max-width: 600px;
            margin-left: auto;
            margin-right: 0;
        }

        .message.bot {
            background: var(--bg-bot);
            padding: 18px 22px;
            border-radius: 4px 16px 16px 16px;
            font-size: 15px;
            line-height: 1.7;
        }

        .message.bot p { margin-bottom: 12px; }
        .message.bot p:last-child { margin-bottom: 0; }

        .message.bot strong {
            color: var(--accent);
            font-weight: 600;
        }

        .sources {
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px solid var(--border);
        }

        .sources-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }

        .source-card {
            display: block;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 8px;
        }

        .source-title {
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
            margin-bottom: 4px;
            line-height: 1.4;
        }

        .source-date {
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 6px;
        }

        .source-links {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }

        .source-links a {
            font-size: 12px;
            color: var(--accent);
            text-decoration: none;
            font-weight: 500;
        }

        .source-links a:hover {
            text-decoration: underline;
        }

        .loading {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-muted);
            font-size: 14px;
            padding: 16px 22px;
            max-width: 720px;
            margin: 0 auto;
        }

        .loading-dots span {
            display: inline-block;
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--accent);
            animation: bounce 1.4s infinite;
        }
        .loading-dots span:nth-child(2) { animation-delay: 0.2s; }
        .loading-dots span:nth-child(3) { animation-delay: 0.4s; }

        @keyframes bounce {
            0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
            40% { transform: scale(1); opacity: 1; }
        }

        .input-area {
            padding: 12px 24px 20px;
            border-top: 1px solid var(--border);
            background: var(--bg);
        }

        .input-wrapper {
            max-width: 720px;
            margin: 0 auto;
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .input-wrapper input {
            flex: 1;
            padding: 14px 18px;
            border: 1px solid var(--border);
            border-radius: 12px;
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            background: var(--bg-chat);
            color: var(--text);
            outline: none;
            transition: border-color 0.2s;
        }

        .input-wrapper input:focus {
            border-color: var(--accent);
        }

        .input-wrapper input::placeholder {
            color: var(--text-muted);
        }

        .input-wrapper button {
            padding: 14px 22px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
            white-space: nowrap;
        }

        .input-wrapper button:hover {
            background: #b3622f;
        }

        .input-wrapper button:disabled {
            background: var(--border);
            cursor: not-allowed;
        }

        .error {
            background: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
            padding: 14px 20px;
            border-radius: 12px;
            font-size: 14px;
            max-width: 720px;
            margin: 0 auto;
        }

        /* Mobile-friendly adjustments */
        @media (max-width: 600px) {
            header { padding: 16px; }
            header h1 { font-size: 20px; }
            .chat-container { padding: 16px; }
            .input-area { padding: 10px 16px 16px; }
            .message.bot { padding: 14px 16px; }
            .suggestion { font-size: 12px; padding: 6px 12px; }
        }
    </style>
</head>
<body>
    <header>
        <h1>10% Happier Explorer</h1>
        <div class="header-right">
            <span class="remaining" id="remaining"></span>
        </div>
    </header>

    <div class="chat-container" id="chat">
        <div class="welcome" id="welcome">
            <h2>What would you like to explore?</h2>
            <p>Ask a question and I'll search through hundreds of podcast episodes to find relevant insights, then summarize what Dan and his guests have said.</p>
            <div class="suggestions">
                <span class="suggestion" onclick="askQuestion(this.textContent)">What does Joseph Goldstein say about impermanence?</span>
                <span class="suggestion" onclick="askQuestion(this.textContent)">How to deal with anxiety</span>
                <span class="suggestion" onclick="askQuestion(this.textContent)">What is metta meditation?</span>
                <span class="suggestion" onclick="askQuestion(this.textContent)">Tips for building a daily practice</span>
            </div>
        </div>
    </div>

    <div class="input-area">
        <div class="input-wrapper">
            <input type="text" id="query" placeholder="Ask about any episode, topic, or guest..."
                   onkeydown="if(event.key==='Enter')sendQuery()" autofocus>
            <button onclick="sendQuery()" id="sendBtn">Ask</button>
        </div>
    </div>

    <script>
        const TOKEN = localStorage.getItem('chatbot_token') || '';

        function askQuestion(text) {
            document.getElementById('query').value = text;
            sendQuery();
        }

        function updateRemaining(n) {
            const el = document.getElementById('remaining');
            if (n !== undefined && n !== null) {
                el.textContent = n + ' questions left today';
            }
        }

        async function sendQuery() {
            const input = document.getElementById('query');
            const btn = document.getElementById('sendBtn');
            const chat = document.getElementById('chat');
            const welcome = document.getElementById('welcome');
            const query = input.value.trim();

            if (!query) return;

            if (welcome) welcome.style.display = 'none';

            const userMsg = document.createElement('div');
            userMsg.className = 'message user';
            userMsg.textContent = query;
            chat.appendChild(userMsg);

            const loading = document.createElement('div');
            loading.className = 'loading';
            loading.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div> Searching transcripts...';
            chat.appendChild(loading);

            input.value = '';
            btn.disabled = true;
            chat.scrollTop = chat.scrollHeight;

            try {
                const resp = await fetch('/ask', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': 'Bearer ' + TOKEN
                    },
                    body: JSON.stringify({query: query})
                });

                const data = await resp.json();
                loading.remove();

                if (data.error) {
                    if (data.error === 'unauthorized') {
                        localStorage.removeItem('chatbot_token');
                        window.location.reload();
                        return;
                    }
                    const errDiv = document.createElement('div');
                    errDiv.className = 'error';
                    errDiv.textContent = data.error;
                    chat.appendChild(errDiv);
                } else {
                    if (data.remaining !== undefined) updateRemaining(data.remaining);

                    const botMsg = document.createElement('div');
                    botMsg.className = 'message bot';

                    let html = data.answer
                        .split('\\n\\n')
                        .filter(p => p.trim())
                        .map(p => '<p>' + p
                            .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                            .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
                            + '</p>')
                        .join('');

                    if (data.sources && data.sources.length > 0) {
                        html += '<div class="sources"><div class="sources-label">Episodes referenced</div>';
                        const seen = new Set();
                        for (const s of data.sources) {
                            if (!seen.has(s.episode_title)) {
                                seen.add(s.episode_title);
                                const title = s.episode_title.length > 80
                                    ? s.episode_title.substring(0, 77) + '...'
                                    : s.episode_title;
                                const transcriptUrl = 'https://podscripts.co/podcasts/ten-percent-happier-with-dan-harris/' + (s.episode_slug || '');
                                const searchTitle = encodeURIComponent('10% Happier ' + s.episode_title);
                                const spotifyUrl = 'https://open.spotify.com/search/' + searchTitle;
                                const appleUrl = 'https://podcasts.apple.com/search?term=' + searchTitle;

                                html += '<div class="source-card">';
                                html += '<div class="source-title">' + title + '</div>';
                                if (s.episode_date) html += '<div class="source-date">' + s.episode_date + '</div>';
                                html += '<div class="source-links">';
                                html += '<a href="' + transcriptUrl + '" target="_blank">Read transcript</a>';
                                html += '<a href="' + spotifyUrl + '" target="_blank">Spotify</a>';
                                html += '<a href="' + appleUrl + '" target="_blank">Apple Podcasts</a>';
                                html += '</div></div>';
                            }
                        }
                        html += '</div>';
                    }

                    botMsg.innerHTML = html;
                    chat.appendChild(botMsg);
                }
            } catch (e) {
                loading.remove();
                const errDiv = document.createElement('div');
                errDiv.className = 'error';
                errDiv.textContent = 'Something went wrong: ' + e.message;
                chat.appendChild(errDiv);
            }

            btn.disabled = false;
            input.focus();
            chat.scrollTop = chat.scrollHeight;
        }
    </script>
</body>
</html>"""


# --- Web Server ---

class ChatHandler(SimpleHTTPRequestHandler):
    search_engine = None
    claude_client = None
    sessions = None
    rate_limiter = None

    def do_GET(self):
        if self.path == "/" or self.path == "":
            # Check if user has a valid session
            token = self._get_token_from_cookie()
            if not PASSWORD:
                # No password set — serve chat directly
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(CHAT_TEMPLATE.encode())
            elif token and self.sessions.is_valid(token):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(CHAT_TEMPLATE.encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(LOGIN_TEMPLATE.encode())
        else:
            self.send_error(404)

    def do_POST(self):
        content_length = int(self.headers["Content-Length"])
        body = self.rfile.read(content_length)
        data = json.loads(body)

        if self.path == "/login":
            password = data.get("password", "")
            if password == PASSWORD:
                token = self.sessions.create_token()
                self._send_json({"token": token})
            else:
                self._send_json({"error": "wrong_password"})

        elif self.path == "/ask":
            # Verify auth
            if PASSWORD:
                auth = self.headers.get("Authorization", "")
                token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
                if not token or not self.sessions.is_valid(token):
                    self._send_json({"error": "unauthorized"})
                    return
            else:
                token = "local"

            # Check rate limit
            allowed, remaining = self.rate_limiter.check(token)
            if not allowed:
                self._send_json({
                    "error": f"You've reached the daily limit of {DAILY_LIMIT} questions. Try again tomorrow!"
                })
                return

            query = data.get("query", "").strip()
            if not query:
                self._send_json({"error": "Please enter a question."})
                return

            try:
                results = self.search_engine.search(query, top_k=TOP_K)
                answer = generate_answer(self.claude_client, query, results)

                sources = [
                    {
                        "episode_title": r["episode_title"],
                        "episode_date": r["episode_date"],
                        "episode_slug": r.get("episode_slug", ""),
                        "guest": r.get("guest", ""),
                    }
                    for r in results
                ]

                self._send_json({
                    "answer": answer,
                    "sources": sources,
                    "remaining": remaining,
                })

            except anthropic.APIError as e:
                self._send_json({"error": f"Claude API error: {str(e)}"})
            except Exception as e:
                self._send_json({"error": f"Error: {str(e)}"})
        else:
            self.send_error(404)

    def _get_token_from_cookie(self):
        """Check for token in query string (fallback for initial page load)."""
        return None  # Token is managed client-side via localStorage + Authorization header

    def _send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, format, *args):
        if "POST /ask" in str(args):
            print(f"  Query received")
        elif "POST /login" in str(args):
            print(f"  Login attempt")


def main():
    # Check for API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        return

    if not PASSWORD:
        print("WARNING: No CHATBOT_PASSWORD set. Running without password protection.")
        print("  Set CHATBOT_PASSWORD env var to enable it.\n")

    # Load chunks
    if not os.path.exists(CHUNKS_FILE):
        print(f"ERROR: {CHUNKS_FILE} not found.")
        return

    print("Loading transcripts...")
    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    print(f"  Loaded {len(chunks)} chunks")

    # Build search engine
    engine = SearchEngine(chunks)

    # Initialize services
    client = anthropic.Anthropic()
    sessions = SessionManager()
    rate_limiter = RateLimiter(DAILY_LIMIT)

    # Set up server
    ChatHandler.search_engine = engine
    ChatHandler.claude_client = client
    ChatHandler.sessions = sessions
    ChatHandler.rate_limiter = rate_limiter

    server = HTTPServer(("0.0.0.0", PORT), ChatHandler)
    print(f"\n{'='*50}")
    print(f"  10% Happier Explorer is running!")
    print(f"  Open http://localhost:{PORT} in your browser")
    if PASSWORD:
        print(f"  Password protection: ON")
    print(f"  Daily question limit: {DAILY_LIMIT}")
    print(f"{'='*50}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
