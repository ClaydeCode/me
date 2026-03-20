#!/bin/sh
# Configure git identity from environment variables at container startup.
# CLAYDE_GIT_NAME defaults to CLAYDE_GITHUB_USERNAME if not set.
# CLAYDE_GIT_EMAIL is required.

GIT_NAME="${CLAYDE_GIT_NAME:-$CLAYDE_GITHUB_USERNAME}"
GIT_EMAIL="${CLAYDE_GIT_EMAIL}"

if [ -z "$GIT_NAME" ]; then
    echo "ERROR: CLAYDE_GIT_NAME (or CLAYDE_GITHUB_USERNAME) must be set" >&2
    exit 1
fi

if [ -z "$GIT_EMAIL" ]; then
    echo "ERROR: CLAYDE_GIT_EMAIL must be set" >&2
    exit 1
fi

git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"

exec /opt/clayde/.venv/bin/clayde "$@"
