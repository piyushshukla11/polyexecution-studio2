#!/bin/sh
set -e

# Fail-closed public ingress check:
# If PES2_BIND_HOST is non-loopback (e.g. 0.0.0.0 or a public interface)
# and PES2_API_KEY is not configured, refuse startup to prevent unauthenticated
# execution exposure.
BIND_HOST="${PES2_BIND_HOST:-127.0.0.1}"

case "$BIND_HOST" in
  127.0.0.1|localhost|::1)
    # Loopback is safe by default
    ;;
  *)
    if [ -z "$PES2_API_KEY" ] && [ "$PES2_INSECURE_NO_AUTH" != "1" ]; then
      echo "Error: Production security violation: PES2_BIND_HOST is set to public interface '$BIND_HOST' without PES2_API_KEY configured." >&2
      echo "Arbitrary code execution must not be exposed on public interfaces without authentication." >&2
      exit 2
    fi
    ;;
esac

exec "$@"
