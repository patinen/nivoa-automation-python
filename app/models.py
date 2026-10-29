"""Input and AI output data contracts."""
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, EmailStr, Field

Category = Literal["tarjouspyyntö", "asiakkaan kysymys", "roskaposti", "muu", "ihmiselle"]


class LeadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)
    message: str = Field(default="", max_length=10000)


class Analysis(BaseModel):
    category: Category
    summary: str = Field(min_length=1, max_length=350)
    ambiguous: bool
    confidence: float = Field(ge=0, le=1)


def normalize_analysis(a: Analysis) -> tuple[str, str]:
    """Review any model-indicated uncertainty; never interpret confidence as calibrated."""
    category = "ihmiselle" if a.ambiguous or a.confidence < 0.80 else a.category
    # Keep exactly one sentence even if the model accidentally returns multiple.
    text = " ".join(a.summary.split())
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    return category, first_sentence
