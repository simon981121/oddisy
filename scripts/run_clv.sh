#!/bin/bash
# Cron: kör clv.py med lås, timeout och tidsstämplad logg i logs/clv.log. Se SCHEDULE.md.
exec "$(cd "$(dirname "$0")" && pwd)/_run.sh" clv clv.py
