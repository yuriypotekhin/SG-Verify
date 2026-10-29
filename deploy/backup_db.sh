#!/bin/bash
# Daily dump of the SG-Verify database (run from cron). Keeps 14 days.
#   deploy/backup_db.sh [backup dir, default deploy/backups]
# Never remove the compose volumes (the volumes flag of `docker compose down`): that deletes the database.
main() {
    set -euo pipefail
    cd "$(dirname "$0")"
    local dir="${1:-./backups}"
    mkdir -p "$dir"
    docker compose exec -T cvat_db pg_dump -U root -d cvat | gzip > "$dir/cvat_$(date +%Y-%m-%d_%H%M).sql.gz"
    find "$dir" -name 'cvat_*.sql.gz' -mtime +14 -delete
}
main "$@"
