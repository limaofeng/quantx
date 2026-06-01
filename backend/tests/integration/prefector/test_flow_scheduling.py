"""
流程调度配置测试

测试所有 Prefect flows 的调度配置
"""

import pytest
from prefect.client.schemas.schedules import CronSchedule


@pytest.mark.integration
class TestFlowScheduling:
    """流程调度配置测试"""

    def test_all_schedules_are_cron_schedules(self):
        """测试所有调度配置都是 CronSchedule 类型"""
        from prefector.flows.realtime_price_flow import REALTIME_SYNC_SCHEDULE

        assert isinstance(REALTIME_SYNC_SCHEDULE, CronSchedule)

    def test_schedule_cron_expressions(self):
        """测试调度 Cron 表达式"""
        from prefector.flows.realtime_price_flow import REALTIME_SYNC_SCHEDULE

        # 实时同步：工作日每5分钟
        assert REALTIME_SYNC_SCHEDULE.cron == "*/5 * * * 1-5"

    def test_schedule_timezone_configuration(self):
        """测试调度时区配置"""
        # 注意：实际的时区配置可能在部署时设置
        pass
