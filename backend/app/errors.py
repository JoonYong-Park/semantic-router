"""FastAPI와 같은 에러 응답 형태({"detail": "..."})를 유지해 프론트 에러 처리를 그대로 쓴다."""

from flask import jsonify


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


def register(app) -> None:
    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify(detail=e.detail), e.status
