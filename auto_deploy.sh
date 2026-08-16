#!/bin/bash
cd /root/LLM-TradeBot || exit 1

# Auto-deploy only when main is checked out.
#
# Without this guard the script hard-resets to origin/main on every run. On any
# feature branch HEAD never equals origin/main, so it destroyed all local work
# every 5 minutes and rebuilt the container each time.
#
# A detached HEAD yields an empty branch name and is treated as "not main",
# which is the safe default.
CURRENT_BRANCH=$(git symbolic-ref --short -q HEAD)

if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "$(date): on branch '${CURRENT_BRANCH:-detached HEAD}', not main — deploy skipped" >> /root/deploy.log
    exit 0
fi

# Fetch latest changes
git fetch origin main

# Check if there are updates
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "$(date): Updates found! Deploying..." >> /root/deploy.log
    git reset --hard origin/main
    cd docker
    docker compose --env-file ../.env up --build -d >> /root/deploy.log 2>&1
    echo "$(date): Deployment completed." >> /root/deploy.log
else
    # echo "No updates found."
    :
fi
