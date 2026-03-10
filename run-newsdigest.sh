#!/bin/bash
cd /Users/prasanthmallaya/ai-brief
set -a
source .env
set +a
/Users/prasanthmallaya/.local/bin/newsdigest --config config.yaml
