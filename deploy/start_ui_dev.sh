#!/bin/bash
# Development: webpack dev server of the SG-Verify UI on top of a running stack (UI changes apply on save).
#   CVAT_SRC=<cvat clone with the patch> API_URL=http://127.0.0.1:8080 PORT=3000 deploy/start_ui_dev.sh
# Needs node >= 20 with corepack (yarn) on PATH; run `yarn install` in the CVAT clone once.
main() {
    set -euo pipefail
    local cvat="${CVAT_SRC:-$(cd "$(dirname "$0")/.." && pwd)/build/cvat}"
    cd "$cvat/cvat-ui"
    export CVAT_UI_HOST=127.0.0.1 CVAT_UI_PORT="${PORT:-3000}"
    export API_URL="${API_URL:-http://127.0.0.1:8080}" SG_TOOLS_URL="${SG_TOOLS_URL:-${API_URL:-http://127.0.0.1:8080}}"
    export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=8192}"
    exec ../node_modules/.bin/webpack serve --env API_URL="$API_URL" --config ./webpack.config.js --mode=development
}
main "$@"
