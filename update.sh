#!/usr/bin/env bash
# update.sh — pull upstream without losing our custom changes.
#
# Usage:
#   ./update.sh          -> pull + rebase, then ASK before rebuilding the container
#   ./update.sh --yes    -> same, but rebuild without asking (also for non-interactive runs)
#   ./update.sh --check  -> just show what upstream has changed, don't apply
#   ./update.sh --stash  -> same as default but stash our uncommitted edits first

set -euo pipefail
cd "$(dirname "$0")"

MODE="apply"
ASSUME_YES=0
for a in "$@"; do
    case "$a" in
        --yes|-y) ASSUME_YES=1 ;;
        *) MODE="$a" ;;
    esac
done

say() { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }

require_clean_tree() {
    if ! git diff --quiet || ! git diff --cached --quiet; then
        err "Working tree has uncommitted changes. Commit or stash first."
        exit 1
    fi
}

# Make sure we have the branches set up.
if ! git rev-parse --verify custom >/dev/null 2>&1; then
    err "Branch 'custom' not found. Run: git checkout -b custom"
    exit 1
fi
if ! git rev-parse --verify master >/dev/null 2>&1; then
    err "Branch 'master' not found."
    exit 1
fi

case "$MODE" in
    --check)
        say "Fetching upstream (no changes applied)"
        git fetch origin
        local_only="$(git log master..custom --oneline | wc -l)"
        upstream_only="$(git log custom..origin/master --oneline | wc -l)"
        say "Local-only commits on custom : $local_only"
        say "Upstream-only commits on master: $upstream_only"
        if [ "$upstream_only" -gt 0 ]; then
            say "Upstream changes since your last sync:"
            git log custom..origin/master --oneline
        fi
        ;;

    --stash)
        say "Stashing any working-tree edits"
        git stash push -u -m "update.sh auto-stash $(date +%s)" || true
        ;&   # fallthrough

    apply|"")
        require_clean_tree
        say "Fetching upstream"
        git fetch origin

        upstream_only="$(git log custom..origin/master --oneline | wc -l)"
        if [ "$upstream_only" -eq 0 ]; then
            say "Already up to date."
            exit 0
        fi

        say "Upstream commits to apply:"
        git log custom..origin/master --oneline

        say "Rebasing 'custom' onto 'origin/master'"
        if ! git rebase origin/master; then
            err "Rebase produced conflicts. Resolve them, then run: docker compose up -d --build"
            exit 1
        fi

        # Never put unreviewed upstream code into production silently: rebuilding means a brief downtime.
        if [ "$ASSUME_YES" -ne 1 ]; then
            if [ -t 0 ]; then
                read -r -p "Rebuild the container now (brief downtime)? [y/N] " ans
                case "$ans" in
                    [Yy]*) ;;
                    *) warn "Rebase done, container NOT rebuilt. Run: docker compose up -d --build"; exit 0 ;;
                esac
            else
                warn "Non-interactive run: container NOT rebuilt (use --yes). Run: docker compose up -d --build"
                exit 0
            fi
        fi
        say "Rebuilding container"
        docker compose up -d --build

        say "Done. Custom changes preserved, upstream applied."
        ;;

    *)
        err "Unknown mode: $MODE (use --check, --stash, or no flag)"
        exit 2
        ;;
esac
