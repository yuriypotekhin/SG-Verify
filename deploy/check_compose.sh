#!/bin/bash
# Pre-flight check of the SG-Verify stack before `docker compose up`. Run it from the deploy/ directory:
#   ./check_compose.sh                  # the base stack
#   ./check_compose.sh --profile gpu    # with the SAM3 tracking service
# Optional environment:
#   SG_CHECK_PORTS      regex that every published port must match (default: any port)
#   SG_CHECK_FORBIDDEN  regex of host paths that must not be bind-mounted (default: none)
# Prints what the stack will create and fails (exit 1) if it could interfere with other stacks on this host:
# traefik labels, container_name, docker.sock, external networks/volumes, names without the project prefix,
# ports not bound to 127.0.0.1.
main() {
    set -uo pipefail
    local cfg project fail=0
    cfg=$(docker compose "$@" config) || { echo "FAIL: docker compose config did not render"; exit 1; }
    project=$(echo "$cfg" | awk '/^name:/ {print $2; exit}')
    echo "project:  $project"
    echo "services: $(echo "$cfg" | awk '/^services:/ {s=1; next} /^[a-z]/ {s=0} s && /^  [a-z_]+:$/ {sub(":", ""); printf "%s ", $1}')"
    echo "images:   $(echo "$cfg" | awk '/^    image:/ {printf "%s ", $2}')"
    echo "restart:  $(echo "$cfg" | awk '/^    restart:/ {print $2}' | sort | uniq -c | tr -s ' ' | tr '\n' ' ')"

    local hits
    hits=$(echo "$cfg" | grep -nE 'traefik\.|container_name')
    if [ -n "$hits" ]; then echo "FAIL: traefik labels or container_name:"; echo "$hits"; fail=1; fi
    hits=$(echo "$cfg" | grep -n 'docker.sock')
    if [ -n "$hits" ]; then echo "FAIL: docker.sock is mounted:"; echo "$hits"; fail=1; fi
    hits=$(echo "$cfg" | grep -nE 'external: *true')
    if [ -n "$hits" ]; then echo "FAIL: external network or volume:"; echo "$hits"; fail=1; fi

    # top-level networks and volumes must carry the project prefix
    local names
    names=$(echo "$cfg" | awk '/^(networks|volumes):/ {s=$1; next} /^[a-z]/ {s=""} s && /^    name:/ {print s, $2}')
    echo "names:"; echo "$names" | sed 's/^/  /'
    hits=$(echo "$names" | awk -v p="${project}_" 'index($2, p) != 1')
    if [ -n "$hits" ]; then echo "FAIL: names without the prefix ${project}_:"; echo "$hits"; fail=1; fi

    # published ports: bound to 127.0.0.1 only
    local ips ports
    ips=$(echo "$cfg" | awk '/^ +host_ip:/ {print $2}')
    ports=$(echo "$cfg" | awk '/^ +published:/ {gsub("\"", "", $2); print $2}')
    echo "ports:    $(paste -d: <(echo "$ips") <(echo "$ports") | tr '\n' ' ')"
    if [ "$(echo "$ips" | grep -c .)" != "$(echo "$ports" | grep -c .)" ] || echo "$ips" | grep -qv '^127\.0\.0\.1$'; then
        echo "FAIL: a port is not bound to 127.0.0.1"; fail=1
    fi
    if [ -n "${SG_CHECK_PORTS:-}" ] && echo "$ports" | grep -qvE "^(${SG_CHECK_PORTS})$"; then
        echo "FAIL: a port does not match ${SG_CHECK_PORTS}"; fail=1
    fi

    # bind mounts from the host
    local binds
    binds=$(echo "$cfg" | awk '/type: bind/ {b=1; next} b && /source:/ {print $2; b=0}')
    echo "binds:"; echo "$binds" | sed 's/^/  /'
    if [ -n "${SG_CHECK_FORBIDDEN:-}" ] && echo "$binds" | grep -qE "${SG_CHECK_FORBIDDEN}"; then
        echo "FAIL: a forbidden host path is mounted:"; echo "$binds" | grep -E "${SG_CHECK_FORBIDDEN}"; fail=1
    fi

    if [ "$fail" = 0 ]; then echo "OK: the stack is isolated"; else echo "NOT OK: do not start this stack"; fi
    return $fail
}
main "$@"
