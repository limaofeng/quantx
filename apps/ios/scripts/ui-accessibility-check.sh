#!/bin/sh
set -eu

if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [simulator-udid]\n' "$0" >&2
  exit 64
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IOS_ROOT=$(dirname "$SCRIPT_DIR")
DEVICE_ID=${1:-${QUANTX_SIMULATOR_ID:-}}

if [ -z "$DEVICE_ID" ]; then
  printf '请通过参数或 QUANTX_SIMULATOR_ID 指定一个可用的 iPhone Simulator UDID。\n' >&2
  printf '可用设备：xcrun simctl list devices available\n' >&2
  exit 64
fi

xcrun simctl boot "$DEVICE_ID" >/dev/null 2>&1 || true
xcrun simctl bootstatus "$DEVICE_ID" -b

ORIGINAL_APPEARANCE=$(xcrun simctl ui "$DEVICE_ID" appearance)
ORIGINAL_CONTENT_SIZE=$(xcrun simctl ui "$DEVICE_ID" content_size)

restore_simulator_ui() {
  xcrun simctl ui "$DEVICE_ID" appearance "$ORIGINAL_APPEARANCE" >/dev/null
  xcrun simctl ui "$DEVICE_ID" content_size "$ORIGINAL_CONTENT_SIZE" >/dev/null
}
trap restore_simulator_ui EXIT INT TERM

run_test() {
  appearance=$1
  content_size=$2
  test_name=$3
  printf '\nQuantX UI check: appearance=%s content_size=%s test=%s\n' \
    "$appearance" "$content_size" "$test_name"
  xcrun simctl ui "$DEVICE_ID" appearance "$appearance"
  xcrun simctl ui "$DEVICE_ID" content_size "$content_size"
  xcodebuild \
    -project "$IOS_ROOT/QuantX.xcodeproj" \
    -scheme QuantX \
    -destination "platform=iOS Simulator,id=$DEVICE_ID" \
    -derivedDataPath "$IOS_ROOT/.build/DerivedData" \
    -only-testing:"QuantXUITests/QuantXUITests/$test_name" \
    test CODE_SIGNING_ALLOWED=NO -quiet
}

run_test light large testDashboardPassesAccessibilityAudit
run_test light accessibility-extra-extra-extra-large \
  testDashboardContentRemainsReachableAtAccessibilityTextSize
run_test dark large testDashboardPassesAccessibilityAudit
run_test dark accessibility-extra-extra-extra-large \
  testDashboardContentRemainsReachableAtAccessibilityTextSize

printf '\nQuantX UI accessibility matrix passed.\n'
