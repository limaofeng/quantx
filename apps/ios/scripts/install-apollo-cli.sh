#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IOS_ROOT=$(dirname "$SCRIPT_DIR")
APOLLO_VERSION="2.1.2"
ARCHIVE=$(mktemp -t quantx-apollo-cli.XXXXXX)

cleanup() {
  rm -f "$ARCHIVE"
}
trap cleanup EXIT INT TERM

curl --fail --location --silent --show-error \
  "https://github.com/apollographql/apollo-ios/releases/download/${APOLLO_VERSION}/apollo-ios-cli.tar.gz" \
  --output "$ARCHIVE"

tar -xzf "$ARCHIVE" -C "$IOS_ROOT" apollo-ios-cli
chmod +x "$IOS_ROOT/apollo-ios-cli"
"$IOS_ROOT/apollo-ios-cli" --version
