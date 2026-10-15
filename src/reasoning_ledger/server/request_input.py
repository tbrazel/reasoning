"""Shared public request validation before text reaches storage or execution."""

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


def _validate_utf8(value: object) -> None:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must contain valid Unicode scalar values") from None
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_utf8(key)
            _validate_utf8(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_utf8(item)


class RequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("*", mode="before")
    @classmethod
    def utf8_text(cls, value: object) -> object:
        # Reject unpaired JSON surrogate escapes without changing valid text,
        # especially password whitespace, combining characters, or secret bytes.
        _validate_utf8(value)
        return value
