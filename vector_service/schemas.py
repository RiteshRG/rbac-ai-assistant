"""Strict request and response schemas for the internal vector API."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=4000)
    top_k: StrictInt = Field(ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def require_string_query(cls, value: object) -> object:
        """Reject non-string input before Pydantic applies string validation.

        Args:
            value: Raw value supplied for the ``query`` field.

        Returns:
            The unchanged value when it is a string.

        Called by: Pydantic while validating a ``SearchRequest`` instance.
        """
        if not isinstance(value, str):
            raise ValueError("query must be a string")
        return value


class SearchResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    access_group: str
    source_file: str
    document_title: str
    section_path: str
    chunk_index: int
    distance: float | None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResultResponse]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
