"""
GraphQL应用模块
提供GraphQL API服务，支持订阅功能
"""

from fastapi import FastAPI
from strawberry.dataloader import DataLoader
from strawberry.fastapi import GraphQLRouter

from .dataloaders.quote_loader import load_quotes
from .schema import schema


async def get_context():
  """获取 GraphQL Context，包含 DataLoader"""
  return {"quote_loader": DataLoader(load_fn=load_quotes)}


def create_graphql_app() -> GraphQLRouter:
  """创建GraphQL应用，支持WebSocket订阅"""
  return GraphQLRouter(
    schema,
    graphiql=True,  # 启用GraphiQL界面
    context_getter=get_context,
  )


def setup_graphql(app: FastAPI) -> None:
  """为FastAPI应用添加GraphQL支持"""
  graphql_app = create_graphql_app()
  app.include_router(graphql_app, prefix="/graphql")
