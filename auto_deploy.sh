#!/bin/bash
cd /root/LLM-TradeBot || exit 1

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
