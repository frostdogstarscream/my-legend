"""所有适配层错误都携带稳定编码和字段路径，便于宿主定位失败。"""
class VisualError(Exception):
    def __init__(self, code, message, field_path=None):
        self.issue = dict(code=code, message=message, field_path=field_path)
        super().__init__(message)
