"""可安全展示的错误；不携带 HTTP 响应或秘密。"""


class ServiceError(Exception):
    def __init__(self, message: str, *, code: str = "operation_failed") -> None:
        super().__init__(message)
        self.code = code


class StaleRequestError(ServiceError):
    def __init__(self) -> None:
        super().__init__("提取请求已过期，请查看当前通知。", code="stale_request")
