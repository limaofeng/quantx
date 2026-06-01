# GraphQL API module initialization
# Avoid eager imports to prevent circular-import issues during backend startup.

__all__ = ["schema", "create_graphql_app", "setup_graphql"]


def __getattr__(name):  # pragma: no cover - module-level lazy import
    if name in __all__:
        from .app import create_graphql_app, setup_graphql
        from .schema import schema
        return {"schema": schema, "create_graphql_app": create_graphql_app, "setup_graphql": setup_graphql}[name]
    raise AttributeError(f"module 'gqlapi' has no attribute '{name}'")
