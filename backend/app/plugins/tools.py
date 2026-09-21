"""Compose trusted backend/template plugins using a declarative tool catalog."""

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..mail import EmailTemplate
from .backends import MAIL_BACKENDS, MailBackendPlugin
from .templates import EMAIL_TEMPLATES

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class EnvValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Identifier
    name: str = Field(min_length=1, max_length=100)
    backend: Identifier
    template: Identifier
    revision: int = Field(default=1, ge=1)
    options: dict[str, str | int | bool | EnvValue] = Field(default_factory=dict)


class ToolCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    default: Identifier | None = None
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True)
class MailTool:
    id: str
    name: str
    backend: MailBackendPlugin
    template: EmailTemplate | None
    config_hash: str


@lru_cache
def tool_catalog() -> ToolCatalog:
    path = Path(
        os.getenv("MAIL_TOOLS_CONFIG_FILE") or Path(__file__).with_name("tools.toml")
    )
    try:
        with path.open("rb") as source:
            raw = source.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError
        catalog = ToolCatalog.model_validate(tomllib.loads(raw.decode()))
        ids = [tool.id for tool in catalog.tools]
        if len(set(ids)) != len(ids) or (
            catalog.default and catalog.default not in ids
        ):
            raise ValueError
        return catalog
    except (OSError, UnicodeError, ValueError, ValidationError):
        # Validation errors may contain deployment configuration; never echo them.
        raise RuntimeError("Invalid mailbox tool configuration") from None


def get_mail_tool(identity: str | None) -> MailTool | None:
    definition = next((t for t in tool_catalog().tools if t.id == identity), None)
    if definition is None:
        return None
    backend = MAIL_BACKENDS.get(definition.backend)
    template = EMAIL_TEMPLATES.get(definition.template)
    if backend is None or template is None:
        return None
    options = {
        key: os.getenv(value.env, "") if isinstance(value, EnvValue) else value
        for key, value in definition.options.items()
    }
    digest = hashlib.sha256(
        json.dumps(
            [
                definition.id,
                definition.backend,
                definition.template,
                definition.revision,
                options,
            ],
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    return MailTool(
        definition.id, definition.name, backend, template.load(options), digest
    )


def default_mail_tool() -> str | None:
    identity = tool_catalog().default
    return identity if get_mail_tool(identity) is not None else None


def mail_tool_options():
    return {
        "default": default_mail_tool(),
        "items": [
            {"id": tool.id, "name": tool.name}
            for definition in tool_catalog().tools
            if (tool := get_mail_tool(definition.id)) is not None
        ],
    }
