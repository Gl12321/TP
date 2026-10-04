import base64
import hashlib
import hmac
import secrets
import asyncio

from cryptography.fernet import Fernet


DUMMY_PASSWORD_HASH = "scrypt$32768$8$3$" + base64.b64encode(bytes(48)).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=128 * 1024 * 1024, dklen=32
    )
    return "scrypt$32768$8$3$" + base64.b64encode(salt + digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, work, block, parallel, content = encoded.split("$")
        if algorithm != "scrypt":
            return False
        if (work, block, parallel) != ("32768", "8", "3"):
            return False
        payload = base64.b64decode(content, validate=True)
        salt, expected = payload[:16], payload[16:]
        digest = hashlib.scrypt(
            password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=128 * 1024 * 1024, dklen=32
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


async def password_work(request, function, *arguments):
    from backend.app.infrastructure.errors import AppError

    gate = request.app.state.password_gate
    try:
        await asyncio.wait_for(gate.acquire(), timeout=0.1)
    except TimeoutError:
        raise AppError(
            "auth_busy", "Сервис входа занят. Повторите попытку через несколько секунд", 429
        )
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    finally:
        gate.release()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), ("csrf:" + token).encode(), hashlib.sha256).hexdigest()


class SecretStore:
    def __init__(self, secret: str):
        key = hashlib.sha256(("sources:" + secret).encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()
