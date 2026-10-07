#!/bin/bash
set -e

BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "$BRANCH" = "main" ]; then
    echo "Already on main, nothing to do."
    exit 0
fi

echo "Merge ${BRANCH} → main"

# Take over what others pushed to the feature branch meanwhile (another clone, a Claude
# session, the version-bump Action). Without this the final push is rejected and what was
# pushed there is missing from main. A merge, never a rebase or force: nothing is rewritten.
# A conflict in VERSION alone is settled by keeping the local file; the version is taken
# from main below anyway.
if git fetch -q origin "$BRANCH" 2>/dev/null; then
    if ! git merge --no-edit "origin/${BRANCH}"; then
        if [ "$(git diff --name-only --diff-filter=U)" = "VERSION" ]; then
            git checkout --ours VERSION
            git add VERSION
            git commit --no-edit
        else
            git merge --abort
            echo "Conflict while merging origin/${BRANCH} into ${BRANCH}. Resolve it by hand, then run again." >&2
            exit 1
        fi
    fi
fi

git switch main

# Sync local main with origin BEFORE attempting the merge so a moving
# remote (e.g. version-bump from a previous merge) doesn't cause the push
# to be rejected as non-fast-forward. Untracked files in working tree
# directories that git wants to populate (e.g. bot/qa_reports/ written by
# the in-container QA runner) would block the implicit checkout, so stash
# them out of the way and restore afterwards.
STASHED=""
if ! git pull --ff-only origin main 2>/dev/null; then
    if [ -n "$(git ls-files --others --exclude-standard)" ]; then
        echo "Stashing untracked files to allow main checkout..."
        git stash push --include-untracked --message "merge-to-main: temp untracked stash" >/dev/null
        STASHED="yes"
    fi
    git pull --ff-only origin main
fi

git merge "$BRANCH"
git push

echo "Waiting for version-bump (GitHub Action)..."
sleep 15
git pull --ff-only origin main
echo "Version: $(cat VERSION)"

# Fast-forward feature branch to main (including version bump).
git branch -f "$BRANCH" main
git switch "$BRANCH"

if [ "$STASHED" = "yes" ]; then
    echo "Restoring stashed untracked files..."
    git stash pop >/dev/null || echo "Stash pop conflict — review with 'git stash list'."
fi

git push
echo "Branch '${BRANCH}' updated to main and pushed."
