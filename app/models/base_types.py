# -*- coding: utf-8 -*-
import uuid, json
from sqlalchemy.types import TypeDecorator, String, Text


class GUID(TypeDecorator):
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError):
            return value

    @staticmethod
    def new():
        return str(uuid.uuid4())


class ArrayOfUUID(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "[]"
        return json.dumps([str(v) for v in value])

    def process_result_value(self, value, dialect):
        if not value:
            return []
        try:
            return [uuid.UUID(v) for v in json.loads(value)]
        except Exception:
            return []


class ArrayOfText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "[]"
        if isinstance(value, str):
            try:
                json.loads(value)
                return value        # 已是合法 JSON
            except Exception:
                pass
        if isinstance(value, list):
            return json.dumps(value)
        return json.dumps([str(value)])

    def process_result_value(self, value, dialect):
        if not value:
            return []
        try:
            return json.loads(value)
        except Exception:
            return []
