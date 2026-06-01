import strawberry
from typing import Optional, List
from models.sector import Sector as SectorModel

@strawberry.type(description="板块分类统计")
class SectorStats:
    classification: str = strawberry.field(description="分类代码")
    count: int = strawberry.field(description="数量")

@strawberry.type(description="行业/概念板块")
class Sector:
    id: int = strawberry.field(description="板块ID")
    name: str = strawberry.field(description="板块名称")
    code: str = strawberry.field(description="板块代码")
    description: Optional[str] = strawberry.field(description="描述")
    classification: str = strawberry.field(description="分类 (SW/TGN/DY等)")
    market: Optional[str] = strawberry.field(description="市场")
    level: int = strawberry.field(description="层级")
    parent_id: Optional[int] = strawberry.field(description="父板块ID")
    
    @strawberry.field(description="子板块")
    def children(self) -> List["Sector"]:
        if not hasattr(self, "_model") or not self._model.children:
            return []
        return [Sector.from_model(c) for c in self._model.children]

    @strawberry.field(description="成分股代码")
    def stock_codes(self) -> List[str]:
        if not hasattr(self, "_model"):
            return []
        return self._model.stock_codes

    @classmethod
    def from_model(cls, model: SectorModel) -> "Sector":
        instance = cls(
            id=model.id,
            name=model.name,
            code=model.code,
            description=model.description,
            classification=model.classification,
            market=model.market,
            level=model.level,
            parent_id=model.parent_id
        )
        instance._model = model
        return instance

@strawberry.type(description="板块查询结果")
class SectorQueryResult:
    items: List["Sector"] = strawberry.field(description="板块列表")
    total: int = strawberry.field(description="总数")
