# football-monitor

An AI-powered football news monitor that scans multiple football news feeds, groups duplicate stories from different sources, scores each story for YouTube Shorts viral potential, and sends Telegram alerts only for the strongest candidates.

## Features

- Monitors ESPN FC, FIFA News, BBC Sport Football, Sky Sports Football, Fabrizio Romano, and The Athletic Football
- Groups duplicate stories from different sources
- Uses OpenAI-compatible models (or a heuristic fallback) to score articles from 0 to 10 for Shorts viral potential
- Sends Telegram notifications only when the score is 8 or higher
- Includes headline, source, score, reason, and article link in the alert
- Runs every 5 minutes
- Keeps a persistent state file to avoid duplicate notifications

## Requirements

- Python 3.12
- feedparser
- requests
- python-dotenv

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows use .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Configuration

Copy [.env.example](.env.example) to .env and fill in your Telegram credentials:

```bash
cp .env.example .env
```

## Usage

Run once:

```bash
python monitor.py --once
```

Run continuously:

```bash
python monitor.py
```

Manual breaking-news mode:

```bash
python monitor.py --manual "Cape Verde goal"
```

This immediately generates a Brazilian Portuguese Shorts package and sends it via Telegram, even if no RSS story is available yet.

## GitHub Actions

A workflow can be added to run the script on a schedule. The repository is structured to support that with a simple `python monitor.py --once` command.

### Repository secrets

This workflow uses GitHub Secrets to securely provide Telegram credentials during execution.

1. Open your repository on GitHub.
2. Go to `Settings` > `Secrets and variables` > `Actions`.
3. Click `New repository secret`.
4. Add the following secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Save the secrets.

Do not commit your local `.env` file or secret values to the repository. The `.gitignore` file already excludes `.env`.
