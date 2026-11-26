from typing import Literal
from pydantic import BaseModel, Field

RULE_VERSION = "1.2.0"


class Finding(BaseModel):
    id: str
    rule_id: str
    category: Literal["documentation", "startup", "portability", "secrets"]
    severity: Literal["high", "medium", "low", "info"]
    confidence: Literal["high", "medium", "low"]
    path: str | None = None
    line: int | None = None
    title: str
    evidence: str = ""
    explanation: str
    suggestion: str
    source_url: str | None = None


class Dimension(BaseModel):
    key: str
    label: str
    score: int
    maximum: int
    applicable: bool = True
    details: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    entries: int = 0
    scanned_files: int = 0
    scanned_bytes: int = 0
    complete: bool = True
    skipped: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Report(BaseModel):
    repository: str
    branch: str
    commit_sha: str
    scanned_at: str
    rule_version: str = RULE_VERSION
    ecosystems: list[str]
    score: int
    assessment: str
    assessment_complete: bool = True
    assessment_notes: list[str] = Field(default_factory=list)
    provisional: bool
    urgent: bool
    coverage: Coverage
    dimensions: list[Dimension]
    findings: list[Finding]
