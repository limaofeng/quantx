"""
行业板块数据同步流程测试

直接执行 sector_data_sync_flow 的集成测试，不使用 Mock
"""

import pytest
import asyncio
from prefector.flows.sector_data_flow import sector_data_sync_flow
from models.sector import Sector
from sqlalchemy import select
from database.connection import get_async_db

@pytest.mark.integration
class TestSectorDataSyncFlow:
    """行业板块数据同步流程集成测试"""

    @pytest.mark.asyncio
    async def test_sector_data_sync_flow_real(self):
        """执行真实同步流程并验证数据库结果"""
        print("\n[INFO] 开始执行真实行业板块同步流程...")
        
        # 直接运行流程
        result = await sector_data_sync_flow()
        
        # 验证返回
        print(f"[INFO] 流程执行完成。状态: {result.get('status')}")
        assert result["status"] == "success", f"同步失败: {result.get('error')}"
        
        fetched = result.get("fetched_count", 0)
        saved = result.get("saved_count", 0)
        print(f"[INFO] 获取到 {fetched} 个板块，成功保存/更新 {saved} 个板块")
        
        assert fetched > 0, "未获取到任何板块数据"
        assert saved > 0, "未保存任何板块数据"

        # 验证数据库中是否包含地域板块 (DY) 且层级正确
        async for db in get_async_db():
            # 随机抽样检查一个层级深入的地域板块
            stmt = select(Sector).filter(Sector.classification == "DY", Sector.level > 1).limit(5)
            res = await db.execute(stmt)
            sample_sectors = res.scalars().all()
            
            if sample_sectors:
                print(f"[INFO] 验证数据库抽样: 发现 {len(sample_sectors)} 个多级地域板块")
                for s in sample_sectors:
                    print(f"  - 板块: {s.name} (Code: {s.code}, Level: {s.level}, ParentID: {s.parent_id})")
                    if s.parent_id:
                        # 验证 parent 是否真实存在
                        parent = await db.get(Sector, s.parent_id)
                        assert parent is not None, f"板块 {s.name} 的父级 ID {s.parent_id} 在数据库中不存在"
                        print(f"    [OK] 父级校验通过: {parent.name}")
            else:
                print("[WARN] 数据库中未发现多级地域板块，请检查 QMT 返回的数据内容")
            
            break

if __name__ == "__main__":
    # 允许手动直接运行此脚本进行测试
    async def run_manual():
        test = TestSectorDataSyncFlow()
        await test.test_sector_data_sync_flow_real()
    
    asyncio.run(run_manual())
