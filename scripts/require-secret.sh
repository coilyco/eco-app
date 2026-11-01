#!/bin/sh
# Fail the step when a named secret arrived empty. Actions substitutes an
# unset secret as "", so the job would otherwise reach the publisher and fail
# far from the cause.

set -eu

name=${1:-}
[ -n "$name" ] || { echo "usage: $0 <ENV_VAR_NAME>" >&2; exit 2; }

value=$(printenv "$name" || true)
[ -n "$value" ] || { echo "$name is required" >&2; exit 1; }
