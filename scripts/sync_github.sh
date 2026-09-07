#!/usr/bin/env bash
# ------------------------------------------------------------------
# sync_github.sh — push local master to the GitHub repo.
# Self-healing: the sandbox may drop .git/config between sessions,
# so this (re)creates origin + credential wiring on every run.
# Credentials come from /home/user/uploads/github_token.txt and are
# NEVER committed anywhere in the repo.
# ------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_URL="https://github.com/caladofficial/sih26051-shelter.git"
TOKEN_FILE="/home/user/uploads/github_token.txt"
CRED_FILE="/home/user/uploads/.github-creds"

# 1) remote origin
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REPO_URL"
  echo "[sync] origin (re)added"
fi

# 2) credentials (global helper so it survives repo .git/config resets)
if git config --global credential.helper >/dev/null 2>&1; then
  : # already configured
elif [ ! -f "$TOKEN_FILE" ]; then
  echo "[sync] ERROR: no token at $TOKEN_FILE" >&2
  exit 1
else
  TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")
  printf 'https://x-access-token:%s@github.com\n' "$TOKEN" > "$CRED_FILE"
  chmod 600 "$CRED_FILE"
  git config --global credential.helper "store --file=$CRED_FILE"
  echo "[sync] credential store configured"
fi

# 3) pull any changes the other device pushed, then push ours
if git fetch -q origin master 2>/dev/null; then
  BEHIND=$(git rev-list --count HEAD..origin/master 2>/dev/null || echo 0)
  if [ "$BEHIND" -gt 0 ]; then
    if git diff --quiet && git diff --cached --quiet; then
      echo "[sync] pulling $BEHIND remote commit(s) first (rebase)"
      git rebase origin/master 2>/dev/null || { echo "[sync] rebase conflict — aborting, resolve manually" >&2; git rebase --abort; exit 1; }
    else
      echo "[sync] WARNING: local changes + remote commits — not auto-merging" >&2
    fi
  fi
fi

git push -q origin master
echo "[sync] pushed: $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
git status -sb | head -1
