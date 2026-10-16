"""Read-only tools that let the assistant answer how-to questions from the CAst guides."""

from __future__ import annotations

from photometry_app.assistant.guides import GuideLibrary
from photometry_app.assistant.program_map import mode_names_for_guide
from photometry_app.assistant.tools import ActionPlan, ToolAccess, ToolContext, ToolDefinition, ToolFailure


def guide_tools(library: GuideLibrary) -> tuple[ToolDefinition, ...]:
    def guide_ids() -> list[str]:
        return [guide.guide_id for guide in library.guides()]

    def search(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        query = str(arguments["query"])
        guide_id = arguments.get("guide")
        if guide_id is not None and library.guide(str(guide_id)) is None:
            raise ToolFailure(f"There is no guide {guide_id!r}. Guides: {', '.join(guide_ids())}.")
        hits = library.search(query, limit=int(arguments.get("max_results", 5)), guide_id=guide_id)
        if not hits:
            return {"results": [], "note": "No guide section matched. Try other words, or say the guides do not cover it."}
        return {
            "results": [
                {"section_id": hit.section.section_id, "guide": hit.section.guide_title, "section": hit.section.path_label, "excerpt": hit.snippet}
                for hit in hits
            ]
        }

    def read_section(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        section_id = str(arguments["section_id"])
        section = library.section(section_id)
        if section is None:
            raise ToolFailure(f"There is no guide section {section_id!r}. Use search_guides or list_guide_sections to find one.")
        text, omitted = library.section_with_subsections(section_id)
        result: dict[str, object] = {"guide": section.guide_title, "section": section.path_label, "text": text}
        if omitted:
            result["more_subsections"] = omitted
        return result

    def list_sections(context: ToolContext, arguments: dict[str, object], _plan: ActionPlan | None) -> object:
        guide_id = arguments.get("guide")
        if guide_id is None:
            return {
                "guides": [
                    {"guide": guide.guide_id, "title": guide.title, "modes": list(mode_names_for_guide(guide.guide_id))}
                    for guide in library.guides()
                ]
            }
        guide = library.guide(str(guide_id))
        if guide is None:
            raise ToolFailure(f"There is no guide {guide_id!r}. Guides: {', '.join(guide_ids())}.")
        return {
            "guide": guide.title,
            "sections": [{"section_id": section.section_id, "level": section.level, "heading": section.heading} for section in guide.sections],
        }

    guide_property = {"type": "string", "maxLength": 64, "description": "Optional guide id, such as hr_diagram. Use list_guide_sections to see them."}
    return (
        ToolDefinition(
            name="search_guides",
            description=(
                "Search the CAst user guides. Use this before answering how-to questions about CAst, "
                "and base the answer on what the guides say."
            ),
            access=ToolAccess.READ,
            run=search,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 300, "description": "What the user wants to do, in a few words."},
                    "guide": guide_property,
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            activity="Searching the guides",
        ),
        ToolDefinition(
            name="read_guide_section",
            description="Read one guide section and its subsections in full, by the section_id from search_guides or list_guide_sections.",
            access=ToolAccess.READ,
            run=read_section,
            parameters={
                "type": "object",
                "properties": {"section_id": {"type": "string", "maxLength": 200}},
                "required": ["section_id"],
                "additionalProperties": False,
            },
            activity="Reading a guide",
        ),
        ToolDefinition(
            name="list_guide_sections",
            description="Without a guide, list the available guides. With a guide id, list its sections and their section_ids.",
            access=ToolAccess.READ,
            run=list_sections,
            parameters={"type": "object", "properties": {"guide": guide_property}, "additionalProperties": False},
            activity="Looking through the guides",
        ),
    )
