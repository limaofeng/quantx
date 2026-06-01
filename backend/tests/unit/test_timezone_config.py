import os
import time
from datetime import datetime

from config.timezone import configure_process_timezone


def test_configure_process_timezone_sets_shared_env_vars():
  active_timezone = configure_process_timezone("Asia/Shanghai")

  assert active_timezone == "Asia/Shanghai"
  assert os.environ["TZ"] == "Asia/Shanghai"
  assert os.environ["TIMEZONE"] == "Asia/Shanghai"
  assert os.environ["TRADING_TIMEZONE"] == "Asia/Shanghai"


def test_configure_process_timezone_affects_local_timestamp_on_supported_platforms():
  configure_process_timezone("Asia/Shanghai")

  if not hasattr(time, "tzset"):
    return

  assert datetime.fromtimestamp(0).hour == 8
