#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IOS_ROOT=$(dirname "$SCRIPT_DIR")
REPO_ROOT=$(CDPATH= cd -- "$IOS_ROOT/../.." && pwd)
CLI="$IOS_ROOT/apollo-ios-cli"
PUBLISHED_SCHEMA_PATH="$REPO_ROOT/apps/docs/public/contracts/graphql-schema.graphql"
SCHEMA_SOURCE=${QUANTX_GRAPHQL_SCHEMA_FILE:-$PUBLISHED_SCHEMA_PATH}
SCHEMA_URL=${QUANTX_GRAPHQL_SCHEMA_URL:-}

if [ ! -x "$CLI" ]; then
  "$SCRIPT_DIR/install-apollo-cli.sh"
fi

if [ -n "$SCHEMA_URL" ]; then
  TEMP_SCHEMA=$(mktemp "${TMPDIR:-/tmp}/quantx-ios-schema.XXXXXX")
  cleanup() {
    rm -f "$TEMP_SCHEMA"
  }
  trap cleanup EXIT INT TERM
  curl --fail --location --silent --show-error --max-time 30 \
    "$SCHEMA_URL" --output "$TEMP_SCHEMA"
  SCHEMA_SOURCE=$TEMP_SCHEMA
fi

case "$SCHEMA_SOURCE" in
  /*) ;;
  *) SCHEMA_SOURCE="$(pwd)/$SCHEMA_SOURCE" ;;
esac

if [ ! -f "$SCHEMA_SOURCE" ]; then
  echo "GraphQL schema source not found: $SCHEMA_SOURCE" >&2
  echo "Set QUANTX_GRAPHQL_SCHEMA_FILE or QUANTX_GRAPHQL_SCHEMA_URL." >&2
  exit 1
fi

if grep -Eq 'accountId: String!? = "[0-9]{8,}"' "$SCHEMA_SOURCE"; then
  echo "Refusing to persist GraphQL schema: an account-like default value was detected." >&2
  echo "Remove sensitive account defaults from the backend schema, then run codegen again." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to prepare the Apollo codegen configuration." >&2
  exit 1
fi

cd "$IOS_ROOT"
CONFIG_JSON=$(
  node -e '
    const fs = require("fs");
    const config = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    config.input.schemaSearchPaths = [process.argv[2]];
    process.stdout.write(JSON.stringify(config));
  ' "$IOS_ROOT/apollo-codegen-config.json" "$SCHEMA_SOURCE"
)
"$CLI" generate --string "$CONFIG_JSON"
