"""Safe authentication errors shared by REST and GraphQL transports."""


class AuthError(Exception):
  def __init__(
    self,
    code: str,
    message: str,
    *,
    status_code: int = 401,
    retryable: bool = False,
  ):
    super().__init__(message)
    self.code = code
    self.message = message
    self.status_code = status_code
    self.retryable = retryable


def unauthenticated(message: str = "认证信息无效或已过期") -> AuthError:
  return AuthError("UNAUTHENTICATED", message, status_code=401)


def forbidden(message: str = "当前会话无权执行此操作") -> AuthError:
  return AuthError("FORBIDDEN", message, status_code=403)
