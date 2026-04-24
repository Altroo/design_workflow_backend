#!/bin/sh
set -e

# Ensure media directories exist with correct permissions
mkdir -p /app/media/user_avatars
chown -R appuser:appuser /app/media

# Drop to appuser and exec the CMD
exec gosu appuser "$@"
