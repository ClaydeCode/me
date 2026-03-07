#!/bin/sh
# Git credential helper that reads the token from config.env.
# Usage: git config --global credential.helper '/home/ubuntu/clayde/git-credential-clayde.sh'

if [ "$1" != "get" ]; then
    exit 0
fi

TOKEN=$(grep '^CLAYDE_GITHUB_TOKEN=' /home/ubuntu/clayde/config.env | cut -d= -f2)

echo "protocol=https"
echo "host=github.com"
echo "username=ClaydeCode"
echo "password=$TOKEN"
