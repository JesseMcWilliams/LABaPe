#!/usr/bin/env bash
# Shared settings for the helpers that talk to the lab host; sourced, not run.
# Values come from the environment (see Live-Testing.local.md for this lab's):
#   LABAPE_LAB_SSH   user@host of the lab host (required)
#   LABAPE_LAB_KEY   SSH private key for it (optional; else ssh's defaults)
#   LABAPE_LAB_URL   the web interface's URL (default https://<host>)
#   LABAPE_LAB_REPO  the stack's git clone on the lab host (default ~/LABaPe-web)
: "${LABAPE_LAB_SSH:?set LABAPE_LAB_SSH to user@host of the lab host (see Live-Testing.local.md)}"
LAB_HOST="${LABAPE_LAB_SSH#*@}"
LAB_URL="${LABAPE_LAB_URL:-https://$LAB_HOST}"
LAB_REPO="${LABAPE_LAB_REPO:-~/LABaPe-web}"
LAB_SSH=(ssh -o BatchMode=yes)
LAB_SCP=(scp -q -o BatchMode=yes)
if [ -n "${LABAPE_LAB_KEY:-}" ]; then
  LAB_SSH+=(-i "$LABAPE_LAB_KEY")
  LAB_SCP+=(-i "$LABAPE_LAB_KEY")
fi
LAB_SSH+=("$LABAPE_LAB_SSH")
