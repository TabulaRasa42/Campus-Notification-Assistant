"""可安全展示给用户的错误，不包含外部响应或密钥。"""


class ServiceError(Exception):
    pass
