#!/bin/bash
sleep 2
cd /home/ulrich/Documents/Projects/jarvis
source .venv/bin/activate
PYTHONUNBUFFERED=1 python3 -m src.server.web_server &
