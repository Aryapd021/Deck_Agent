from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as PydField, field_validator, model_validator

from app.models.deck import Field, Source


class NodeKind(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    DB = "db"
    AI = "ai"
    EXTERNAL = "external"
    INFRA = "infra"


class Node(BaseModel):
    id: str
    label: str
    kind: NodeKind
    group: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = PydField(alias="from")
    to: str
    label: str | None = None


class ComponentGraph(BaseModel):
    nodes: list[Node]
    edges: list[Edge] = PydField(default_factory=list)

    @field_validator("nodes")
    @classmethod
    def _validate_nodes(cls, v: list[Node]) -> list[Node]:
        if not (4 <= len(v) <= 8):
            raise ValueError(f"ComponentGraph must contain 4 to 8 nodes (got {len(v)})")
        node_ids = [node.id for node in v]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ComponentGraph node ids must be unique")
        return v

    @model_validator(mode="after")
    def _validate_edges(self) -> "ComponentGraph":
        valid_ids = {node.id for node in self.nodes}
        for edge in self.edges:
            if edge.from_ not in valid_ids:
                raise ValueError(
                    f"Edge from_ id {edge.from_!r} does not reference an existing node id"
                )
            if edge.to not in valid_ids:
                raise ValueError(
                    f"Edge to id {edge.to!r} does not reference an existing node id"
                )
        return self


class ProjectFacts(BaseModel):
    name: str
    one_liner: Field
    problem: list[Field]
    solution: list[Field]
    features: list[Field]
    stack: list[Field]
    demo_steps: list[Field]
    impact: list[Field]
    roadmap: list[Field]
    components: ComponentGraph


class ChatDigest(BaseModel):
    problem_framing: list[str]
    decisions: list[str]
    rejected_options: list[str]
    insights: list[str]


class RepoDigest(BaseModel):
    name: str
    source_kind: Literal["url", "path"]
    tree: str
    languages: dict[str, int]
    frameworks: list[str]
    manifests: dict[str, str]
    readme_excerpt: str | None = None
    entrypoints: dict[str, str]
    route_files: dict[str, str]
    files_included: list[str]
    known_paths: list[str]
    approx_tokens: int
