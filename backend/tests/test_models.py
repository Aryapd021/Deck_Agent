import pytest
from pydantic import ValidationError

from app.models import (
    AddSlide,
    ComponentGraph,
    CreateProjectRequest,
    Deck,
    DeckDraft,
    DeleteSlide,
    Edge,
    EditPlan,
    Field,
    FitLimits,
    LayoutInfo,
    MoveSlide,
    Node,
    NodeKind,
    PlaceholderInfo,
    Regenerate,
    SetDiagramFocus,
    ShortenItem,
    ShortenResponse,
    Slide,
    SlidePatch,
    SlideType,
    SlotMap,
    Source,
    TemplateManifest,
    UpdateSlide,
)


def _make_field(text: str = "Test Field", source: Source = Source.USER) -> Field:
    return Field(text=text, source=source)


def _make_nodes(count: int) -> list[Node]:
    kinds = [NodeKind.FRONTEND, NodeKind.BACKEND, NodeKind.DB, NodeKind.AI]
    return [
        Node(id=f"node_{i}", label=f"Node {i}", kind=kinds[i % len(kinds)])
        for i in range(count)
    ]


def test_field_text_validation():
    # Stripped text
    f = Field(text="  hello world  ", source=Source.REPO, evidence="README.md")
    assert f.text == "hello world"
    assert f.source == Source.REPO
    assert f.evidence == "README.md"

    # Empty text fails
    with pytest.raises(ValidationError):
        Field(text="", source=Source.USER)

    # Whitespace-only text fails
    with pytest.raises(ValidationError):
        Field(text="   \n\t  ", source=Source.USER)

    # Non-string text fails
    with pytest.raises(ValidationError):
        Field(text=123, source=Source.USER)  # type: ignore


def test_slide_bullets_validation():
    # 0 bullets passes
    s0 = Slide(type=SlideType.TITLE, title=_make_field("Title"), bullets=[])
    assert len(s0.bullets) == 0

    # 5 bullets passes
    bullets_5 = [_make_field(f"Bullet {i}") for i in range(5)]
    s5 = Slide(type=SlideType.PROBLEM, title=_make_field("Title"), bullets=bullets_5)
    assert len(s5.bullets) == 5

    # 6 bullets fails
    bullets_6 = [_make_field(f"Bullet {i}") for i in range(6)]
    with pytest.raises(ValidationError) as exc_info:
        Slide(type=SlideType.PROBLEM, title=_make_field("Title"), bullets=bullets_6)
    assert "at most 5 bullets" in str(exc_info.value)


def test_component_graph_validation():
    # 3 nodes fails (must be 4 to 8)
    with pytest.raises(ValidationError) as exc_info:
        ComponentGraph(nodes=_make_nodes(3))
    assert "4 to 8 nodes" in str(exc_info.value)

    # 9 nodes fails (must be 4 to 8)
    with pytest.raises(ValidationError) as exc_info:
        ComponentGraph(nodes=_make_nodes(9))
    assert "4 to 8 nodes" in str(exc_info.value)

    # Duplicate node ids fails
    dup_nodes = [
        Node(id="n1", label="N1", kind=NodeKind.FRONTEND),
        Node(id="n1", label="Duplicate", kind=NodeKind.BACKEND),
        Node(id="n2", label="N2", kind=NodeKind.DB),
        Node(id="n3", label="N3", kind=NodeKind.AI),
    ]
    with pytest.raises(ValidationError) as exc_info:
        ComponentGraph(nodes=dup_nodes)
    assert "unique" in str(exc_info.value)

    # Edge referencing unknown 'from' node fails
    nodes_4 = _make_nodes(4)
    with pytest.raises(ValidationError) as exc_info:
        ComponentGraph(
            nodes=nodes_4,
            edges=[Edge(from_="unknown_node", to="node_0")],
        )
    assert "does not reference an existing node id" in str(exc_info.value)

    # Edge referencing unknown 'to' node fails
    with pytest.raises(ValidationError) as exc_info:
        ComponentGraph(
            nodes=nodes_4,
            edges=[Edge(from_="node_0", to="unknown_node")],
        )
    assert "does not reference an existing node id" in str(exc_info.value)

    # Valid graph with 4 nodes passes
    valid_graph = ComponentGraph(
        nodes=nodes_4,
        edges=[Edge(from_="node_0", to="node_1", label="calls")],
    )
    assert len(valid_graph.nodes) == 4
    assert len(valid_graph.edges) == 1


def test_edge_alias_serialization():
    # Parse from JSON using alias "from"
    json_data = '{"from": "node_a", "to": "node_b", "label": "sends"}'
    edge = Edge.model_validate_json(json_data)
    assert edge.from_ == "node_a"
    assert edge.to == "node_b"
    assert edge.label == "sends"

    # Serialize back with by_alias=True
    dumped = edge.model_dump(by_alias=True)
    assert "from" in dumped
    assert "from_" not in dumped
    assert dumped["from"] == "node_a"

    json_dump = edge.model_dump_json(by_alias=True)
    assert '"from":"node_a"' in json_dump or '"from": "node_a"' in json_dump


def test_edit_plan_mixed_ops_discriminator():
    data = {
        "ops": [
            {
                "op": "update_slide",
                "slide_id": "s_update",
                "fields": {
                    "title": {"text": "Updated Title", "source": "user"},
                    "bullets": [{"text": "New bullet", "source": "user"}],
                },
            },
            {
                "op": "move_slide",
                "slide_id": "s_move",
                "to_index": 2,
            },
            {
                "op": "delete_slide",
                "slide_id": "s_delete",
            },
            {
                "op": "add_slide",
                "after_slide_id": "s_after",
                "type": "solution",
                "content": {
                    "title": {"text": "Added Slide", "source": "voice"},
                },
            },
            {
                "op": "set_diagram_focus",
                "node_ids": ["n1", "n2"],
            },
            {
                "op": "regenerate",
                "slide_id": "s_regen",
            },
        ],
        "message": "Batch edit test",
    }

    plan = EditPlan.model_validate(data)
    assert len(plan.ops) == 6
    assert isinstance(plan.ops[0], UpdateSlide)
    assert plan.ops[0].op == "update_slide"
    assert plan.ops[0].fields.title is not None
    assert plan.ops[0].fields.title.text == "Updated Title"

    assert isinstance(plan.ops[1], MoveSlide)
    assert plan.ops[1].to_index == 2

    assert isinstance(plan.ops[2], DeleteSlide)
    assert plan.ops[2].slide_id == "s_delete"

    assert isinstance(plan.ops[3], AddSlide)
    assert plan.ops[3].type == SlideType.SOLUTION

    assert isinstance(plan.ops[4], SetDiagramFocus)
    assert plan.ops[4].node_ids == ["n1", "n2"]

    assert isinstance(plan.ops[5], Regenerate)
    assert plan.ops[5].slide_id == "s_regen"


def test_deck_with_diagram_round_trip():
    nodes = _make_nodes(4)
    edges = [
        Edge(from_="node_0", to="node_1"),
        Edge(from_="node_1", to="node_2"),
        Edge(from_="node_1", to="node_3"),
    ]
    graph = ComponentGraph(nodes=nodes, edges=edges)

    slide = Slide(
        type=SlideType.ARCHITECTURE,
        title=_make_field("System Architecture", source=Source.REPO),
        bullets=[_make_field("Architecture overview", source=Source.REPO)],
        image_ref="diagram",
        notes="Presenter notes for architecture",
    )

    deck = Deck(
        schema_version=1,
        project_id="proj_xyz",
        version=1,
        template_id="tmpl_abc",
        slides=[slide],
        diagram=graph,
    )

    json_str = deck.model_dump_json(by_alias=True)
    restored = Deck.model_validate_json(json_str)

    assert restored.schema_version == 1
    assert restored.project_id == "proj_xyz"
    assert restored.version == 1
    assert restored.template_id == "tmpl_abc"
    assert len(restored.slides) == 1
    assert restored.slides[0].type == SlideType.ARCHITECTURE
    assert restored.slides[0].image_ref == "diagram"
    assert restored.diagram is not None
    assert len(restored.diagram.nodes) == 4
    assert len(restored.diagram.edges) == 3
    assert restored.diagram.edges[0].from_ == "node_0"


def test_deck_draft_validates():
    slide = Slide(
        type=SlideType.TITLE,
        title=_make_field("Project One-Liner", source=Source.VOICE),
        subtitle=_make_field("Subtitle here", source=Source.VOICE),
    )
    draft = DeckDraft(slides=[slide])
    assert len(draft.slides) == 1
    assert draft.slides[0].type == SlideType.TITLE
    assert draft.slides[0].subtitle is not None
    assert draft.slides[0].subtitle.text == "Subtitle here"


def test_create_project_request():
    # Only repo_url
    req1 = CreateProjectRequest(name="Proj1", repo_url="https://github.com/a/b", template_id="t1")
    assert req1.repo_url == "https://github.com/a/b"
    assert req1.repo_path is None

    # Only repo_path
    req2 = CreateProjectRequest(name="Proj2", repo_path="./local_repo", template_id="t1")
    assert req2.repo_path == "./local_repo"
    assert req2.repo_url is None

    # Both provided fails
    with pytest.raises(ValidationError):
        CreateProjectRequest(
            name="Proj3",
            repo_url="https://github.com/a/b",
            repo_path="./local_repo",
            template_id="t1",
        )

    # Neither provided fails
    with pytest.raises(ValidationError):
        CreateProjectRequest(name="Proj4", template_id="t1")


def test_shorten_response():
    item = ShortenItem(slide_id="s1", field="bullets[0]", text="Short text")
    res = ShortenResponse(items=[item])
    assert len(res.items) == 1
    assert res.items[0].text == "Short text"


def test_template_manifest():
    manifest = TemplateManifest(
        template_id="tmpl_123",
        name="Default Template",
        slide_width=12192000,
        slide_height=6858000,
        layouts=[
            LayoutInfo(
                index=0,
                name="Title Slide",
                placeholders=[
                    PlaceholderInfo(
                        idx=0,
                        type=1,
                        name="Title 1",
                        left=100,
                        top=100,
                        width=500,
                        height=100,
                        char_limit=70,
                    )
                ],
            )
        ],
        layout_map={"title": 0, "problem": 1},
        slots={"title": SlotMap(title_idx=0)},
        limits={"title": FitLimits(title_max=60)},
        palette={"primary": "#1E3A8A"},
        warnings=["Non-standard layout detected"],
    )
    assert manifest.layout_map["title"] == 0
    assert manifest.slots["title"].title_idx == 0
    assert manifest.limits["title"].title_max == 60
    assert manifest.warnings == ["Non-standard layout detected"]
