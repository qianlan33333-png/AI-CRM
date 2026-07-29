#!/usr/bin/env bash
set -euo pipefail

runtime_generation_marker="/home/ubuntu/.aicrm-queue-runtime-generation.env"
if sudo test -e "$runtime_generation_marker" || sudo test -L "$runtime_generation_marker"; then
  if sudo test -L "$runtime_generation_marker" || ! sudo test -f "$runtime_generation_marker"; then
    echo "queue runtime generation marker must be one regular non-symlink file"
    exit 1
  fi
  sudo chown --no-dereference ubuntu:ubuntu "$runtime_generation_marker"
  chmod 0600 "$runtime_generation_marker"
  if [ -L "$runtime_generation_marker" ] \
    || [ ! -f "$runtime_generation_marker" ] \
    || [ ! -r "$runtime_generation_marker" ] \
    || [ "$(stat -c '%U:%G:%a' "$runtime_generation_marker")" != "ubuntu:ubuntu:600" ]; then
    echo "queue runtime generation marker ownership repair failed closed"
    exit 1
  fi
  echo "queue runtime generation marker ownership normalized"
fi
