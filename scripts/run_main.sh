#!/bin/bash
# Cron: kör main.py med lås, timeout och tidsstämplad logg i logs/main.log. Se SCHEDULE.md.
exec "$(cd "$(dirname "$0")" && pwd)/_run.sh" main main.py
