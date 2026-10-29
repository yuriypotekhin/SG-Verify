#!/bin/bash
# SG-Verify installer: builds the images and starts the stack. Run from the repository root:
#   deploy/install.sh              # build the UI image, start CVAT + SG-Verify UI
#   deploy/install.sh --gpu        # also build and start the SAM3 tracking service (needs an NVIDIA GPU)
#   deploy/install.sh --no-build   # images are already there (built elsewhere and loaded with docker load)
#   deploy/install.sh --no-start   # build only
# Settings: deploy/.env (created from deploy/.env.example on the first run).
main() {
    set -euo pipefail
    local repo build=1 start=1 gpu=0 arg
    repo=$(cd "$(dirname "$0")/.." && pwd)
    for arg in "$@"; do
        case "$arg" in
            --gpu) gpu=1 ;;
            --no-build) build=0 ;;
            --no-start) start=0 ;;
            *) echo "unknown option: $arg"; exit 2 ;;
        esac
    done
    cd "$repo/deploy"
    [ -f .env ] || { cp .env.example .env; echo "created deploy/.env from .env.example"; }

    # team password for name-only sign-in: generated once, kept in .env (not in the repository)
    if grep -q '^SG_VERIFY_NAME_ONLY=1' .env && ! grep -q '^SG_VERIFY_SHARED_PASSWORD=.' .env; then
        local pw
        pw=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
        sed -i "s/^SG_VERIFY_SHARED_PASSWORD=.*/SG_VERIFY_SHARED_PASSWORD=$pw/" .env
        echo "generated SG_VERIFY_SHARED_PASSWORD in deploy/.env"
    fi
    set -a; . ./.env; set +a
    local profile=()
    [ "$gpu" = 1 ] && profile=(--profile gpu)

    if [ "$build" = 1 ]; then
        # CVAT v2.75.0 sources + the SG-Verify patch -> UI image
        local cvat="${SG_VERIFY_CVAT_SRC:-$repo/build/cvat}"
        if [ ! -d "$cvat/.git" ]; then
            git clone --branch v2.75.0 --depth 1 https://github.com/cvat-ai/cvat.git "$cvat"
        fi
        if git -C "$cvat" apply --reverse --check "$repo/patch/cvat-v2.75.0-sg-verify.patch" 2>/dev/null; then
            echo "the SG-Verify patch is already applied in $cvat"
        else
            git -C "$cvat" apply "$repo/patch/cvat-v2.75.0-sg-verify.patch"
            echo "applied the SG-Verify patch to $cvat"
        fi
        docker build -f "$cvat/Dockerfile.ui" \
            --build-arg SG_VERIFY_SHARED_PASSWORD="${SG_VERIFY_SHARED_PASSWORD:-}" \
            -t "${SG_VERIFY_UI_IMAGE:-sg-verify/ui:v0.1}" "$cvat"

        if [ "$gpu" = 1 ]; then
            [ -d "$repo/sam-service/sam3" ] || git clone https://github.com/facebookresearch/sam3 "$repo/sam-service/sam3"
            docker build -t "${SG_VERIFY_SAM_IMAGE:-sg-verify/sam-service:v0.1}" "$repo/sam-service"
        fi
    fi

    mkdir -p "${SG_VERIFY_MODELS_DIR:-./models}" "${SG_VERIFY_PACKAGES_DIR:-./packages}"
    if [ "$gpu" = 1 ] && [ ! -f "${SG_VERIFY_MODELS_DIR:-./models}/sam3.pt" ]; then
        echo "WARNING: ${SG_VERIFY_MODELS_DIR:-./models}/sam3.pt not found: download the SAM3 weights first (README, 'Tracking service')"
    fi

    [ "$start" = 1 ] || exit 0
    ./check_compose.sh "${profile[@]}"
    docker compose "${profile[@]}" up -d
    cat <<EOF

SG-Verify is starting on http://127.0.0.1:${SG_VERIFY_PORT:-8080} (the CVAT server needs a few minutes on the first start).
Create the administrator account (once):
  cd deploy && docker compose exec cvat_server bash -c 'python3 ~/manage.py createsuperuser'
Reviewers then sign in with a name only.
EOF
}
main "$@"
