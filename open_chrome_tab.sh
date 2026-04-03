#!/bin/bash
# Script to open a new Chrome tab

# Check if Chrome is already running
if pgrep -x "chrome" > /dev/null || pgrep -x "google-chrome" > /dev/null; then
    # Chrome is running, open new tab
    google-chrome --new-tab
else
    # Chrome not running, start it
    google-chrome &
fi

echo "Chrome tab opened successfully!"