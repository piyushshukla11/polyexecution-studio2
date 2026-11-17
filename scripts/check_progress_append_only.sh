#!/usr/bin/env bash
# Blocks any commit that deletes, modifies, or inserts lines into historical PROGRESS.md entries.
set -euo pipefail

F="PROGRESS.md"

# First commit check: if HEAD does not exist, nothing to compare
git rev-parse --verify HEAD >/dev/null 2>&1 || exit 0

# Check 1: Deletions or in-place modifications (git diff --numstat reports deleted count)
deleted=$(git diff --cached --numstat -- "$F" 2>/dev/null | awk '{print $2}')
if [ -n "${deleted:-}" ] && [ "$deleted" != "0" ]; then
    echo "BLOCKED: $F is strictly append-only. $deleted line(s) removed or changed."
    echo "Add a CORRECTION entry instead of editing history."
    exit 1
fi

# Check 2: Insertions in the middle of existing lines
total_lines=$(git show HEAD:"$F" 2>/dev/null | wc -l | tr -d " ")
if [ -n "$total_lines" ] && [ "$total_lines" -gt 0 ]; then
    while read -r hunk; do
        if [[ "$hunk" =~ @@\ -([0-9]+) ]]; then
            start_line="${BASH_REMATCH[1]}"
            if [ "$start_line" -lt "$total_lines" ]; then
                echo "BLOCKED: $F is append-only. Insertion detected at line $start_line (file has $total_lines lines in HEAD)."
                echo "All new entries must be appended to the end of the file."
                exit 1
            fi
        fi
    done < <(git diff --cached -U0 -- "$F" 2>/dev/null | grep "^@@" || true)
fi

exit 0
