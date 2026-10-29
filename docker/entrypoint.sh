#!/bin/sh
# Container entrypoint. Routes the /smart power-control subcommands to
# scripts/ac_smart.py (HTTP, works without ECHONET) and everything else to the
# mitsubishi-echonet CLI (ECHONET Lite, UDP 3610).
#
#   smart-status  -> ac_smart.py status   (the CLI's own "status" stays ECHONET)
#   on | off | set | monitor | echonet-test -> ac_smart.py
#   anything else -> mitsubishi-echonet CLI (enable, disable, capabilities, ...)
#
# Global options (--ip, --timeout) parse in any position, so
# `docker run ... set --ip 192.168.1.50 --mode cool` works as-is.
case "${1:-}" in
  smart-status)
    shift
    exec python3 /app/ac_smart.py status "$@"
    ;;
  on | off | set | monitor | echonet-test)
    exec python3 /app/ac_smart.py "$@"
    ;;
  *)
    exec mitsubishi-echonet "$@"
    ;;
esac
