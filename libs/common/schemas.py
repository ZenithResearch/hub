from __future__ import annotations

from typing import Any, Mapping, Optional

from google.protobuf import json_format
from google.protobuf.message import Message
from google.protobuf.struct_pb2 import Struct
from pydantic import BaseModel, Field


JsonDict = dict[str, Any]


def struct_to_dict(value: Optional[Struct]) -> JsonDict:
    if value is None:
        return {}
    return json_format.MessageToDict(value, preserving_proto_field_name=True)


def dict_to_struct(value: Optional[Mapping[str, Any]]) -> Struct:
    out = Struct()
    if value:
        json_format.ParseDict(dict(value), out)
    return out


def message_to_dict(msg: Message) -> JsonDict:
    return json_format.MessageToDict(msg, preserving_proto_field_name=True)


class HttpMessageIn(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    metadata: Optional[JsonDict] = None


class HttpMessageOut(BaseModel):
    request_id: str
    status: str
    runtime_response: Optional[JsonDict] = None


class HttpStreamQuery(BaseModel):
    request_id: str = Field(min_length=1)


class HttpSearchKbIn(BaseModel):
    query: str = Field(min_length=1)
    doc_types: Optional[list[str]] = None
    k: int = Field(default=5, ge=1, le=50)
    metadata: Optional[JsonDict] = None


class HttpInvokeToolIn(BaseModel):
    tool_name: str = Field(min_length=1)
    input: JsonDict = Field(default_factory=dict)
    metadata: Optional[JsonDict] = None


class GrpcSubmitUserMessageIn(BaseModel):
    request_id: str = Field(default="")
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    metadata: Optional[JsonDict] = None


class GrpcStreamEventsIn(BaseModel):
    request_id: str = Field(min_length=1)
    metadata: Optional[JsonDict] = None


class GrpcSearchKnowledgeIn(BaseModel):
    request_id: str = Field(default="")
    query: str = Field(min_length=1)
    doc_types: list[str] = Field(default_factory=list)
    k: int = Field(default=5, ge=1, le=50)
    metadata: Optional[JsonDict] = None


class GrpcInvokeToolIn(BaseModel):
    request_id: str = Field(default="")
    tool_name: str = Field(min_length=1)
    input: JsonDict = Field(default_factory=dict)
    metadata: Optional[JsonDict] = None


class GrpcRunToolIn(GrpcInvokeToolIn):
    pass

