"""Talking to a Microduck: JSON-RPC 2.0 over a transport, and a typed client on top."""

from .client import DuckClient, SKILLS, SOUND_TAGS
from .rpc import Rpc, RpcError, RpcTimeout

__all__ = ["DuckClient", "Rpc", "RpcError", "RpcTimeout", "SKILLS", "SOUND_TAGS"]
