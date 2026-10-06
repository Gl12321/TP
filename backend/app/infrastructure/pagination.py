import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Query, Request
from sqlalchemy import and_, or_

from backend.app.infrastructure.database import aware
from backend.app.infrastructure.errors import AppError


class Pagination:
    def __init__(
        self,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        q: Annotated[str, Query(max_length=200)] = "",
    ):
        self.request, self.limit, self.cursor, self.q = request, limit, cursor, q.strip()
        key = hashlib.sha256(("pagination:" + request.app.state.settings.secret_key).encode())
        self.cipher = Fernet(base64.urlsafe_b64encode(key.digest()))

    def scope(self):
        filters = sorted(
            (key, value)
            for key, value in self.request.query_params.multi_items()
            if key not in {"cursor", "limit"}
        )
        context = [self.request.url.path, self.request.state.actor_id, filters]
        return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:24]

    def after(self):
        if self.cursor is None:
            return None
        try:
            scope, timestamp, item_id = json.loads(self.cipher.decrypt(self.cursor.encode()))
            if scope != self.scope() or not isinstance(item_id, str) or not 1 <= len(item_id) <= 36:
                raise ValueError
            when = datetime.fromisoformat(timestamp)
            if when.tzinfo is None:
                raise ValueError
            return when.astimezone(timezone.utc), item_id
        except (InvalidToken, ValueError, TypeError, UnicodeError) as error:
            raise AppError(
                "invalid_cursor", "Обновите список: курсор страницы недействителен"
            ) from error

    def encode(self, position):
        timestamp, item_id = position
        payload = [self.scope(), aware(timestamp).astimezone(timezone.utc).isoformat(), item_id]
        return self.cipher.encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()

    def search(self, *columns):
        escaped = self.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return or_(*(column.ilike("%" + escaped + "%", escape="\\") for column in columns))


Page = Annotated[Pagination, Depends()]


async def paginate(db, statement, model, timestamp, page, response, render):
    position = page.after()
    items, scanned = [], 0
    while scanned < 500:
        query = statement
        if position is not None:
            when, item_id = position
            query = query.where(or_(timestamp < when, and_(timestamp == when, model.id < item_id)))
        batch_size = min(100, 500 - scanned)
        rows = (
            await db.execute(
                query.order_by(None)
                .order_by(timestamp.desc(), model.id.desc())
                .limit(batch_size + 1)
            )
        ).all()
        for index, row in enumerate(rows[:batch_size]):
            record = row[0]
            position = getattr(record, timestamp.key), record.id
            scanned += 1
            payload = await render(row)
            if payload is not None:
                items.append(payload)
            if len(items) == page.limit:
                if index + 1 < len(rows):
                    response.headers["X-Next-Cursor"] = page.encode(position)
                return items
        if len(rows) <= batch_size:
            return items
    response.headers["X-Next-Cursor"] = page.encode(position)
    return items
