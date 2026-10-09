"""单次操作内缓存静态类型解析；文件读取、散列和版本校验照常执行。"""

from contextvars import ContextVar
import copy
from functools import wraps


_cache = ContextVar("fc27_schema_cache", default=None)


def scoped(function):
    @wraps(function)
    def run(*args, **kwargs):
        if _cache.get() is not None:
            return function(*args, **kwargs)
        token = _cache.set({"values": {}, "hits": 0, "misses": 0})
        try:
            return function(*args, **kwargs)
        finally:
            _cache.reset(token)
    return run


def cached(key, factory):
    cache = _cache.get()
    if cache is None:
        return factory()
    if key in cache["values"]:
        cache["hits"] += 1
        return copy.deepcopy(cache["values"][key])
    cache["misses"] += 1
    result = factory()
    if len(cache["values"]) >= 4:
        cache["values"].pop(next(iter(cache["values"])))
    cache["values"][key] = copy.deepcopy(result)
    return result


def stats():
    cache = _cache.get()
    return {"hits": cache["hits"], "misses": cache["misses"]} if cache is not None else {"hits": 0, "misses": 0}
