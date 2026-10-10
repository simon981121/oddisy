#!/bin/bash
# Cron: kör results.py med lås, timeout och tidsstämplad logg i logs/results.log. Se SCHEDULE.md.
exec "$(cd "$(dirname "$0")" && pwd)/_run.sh" results results.py
