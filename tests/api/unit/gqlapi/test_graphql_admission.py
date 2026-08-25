import pytest
from quantx_api.gqlapi.admission import (
  GraphQLQueryAdmission,
  graphql_request_identity,
)


def test_graphql_request_identity_only_admits_query_operations() -> None:
  query = graphql_request_identity(
    b'{"query":"query Safety { accountExecutionSafety(accountId: \\"A\\") { healthStatus } }"}'
  )
  mutation = graphql_request_identity(
    b'{"query":"mutation Stop { revokeAgentDevice(deviceId: \\"D\\") { success } }"}'
  )

  assert query.is_query
  assert query.operation_name == "Safety"
  assert not mutation.is_query
  assert mutation.operation_name == "Stop"
  assert not graphql_request_identity(b"not-json").is_query


@pytest.mark.asyncio
async def test_graphql_query_admission_releases_capacity() -> None:
  admission = GraphQLQueryAdmission(capacity=1)

  assert await admission.acquire() is not None
  admission.release()
  assert await admission.acquire() is not None
  admission.release()
