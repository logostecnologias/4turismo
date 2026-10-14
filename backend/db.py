import os
from motor.motor_asyncio import AsyncIOMotorClient

_client = None
_db = None


def get_db():
    global _client, _db
    if _db is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        _db = _client[os.environ["DB_NAME"]]
    return _db


def close_db():
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
