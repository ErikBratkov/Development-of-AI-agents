"""Адаптер объектной модели в плоское графовое представление.

Единственное место, где доменные обьекты превращаются в узлы
и связи. Другие форматы выгрузки (JSON, GraphML) добавляются
рядом такими же адаптерами, не трогая генератор.
"""

from typing import Any

from graph_generator.models import Dataset, Node, Relation


def build_graph(dataset: Dataset) -> tuple[list[Node], list[Relation]]:
    """Строит списки узлов и связей из объектной модели"""
    nodes: list[Node] = []
    node_ids: dict[int, int] = {}

    # ключ словаря - id() обьекта, а не сам обьект, чтобы случайно
    # совпавшие по значению экземпляры не склеивались в один узел
    labeled: list[tuple[str, object, dict[str, Any]]] = []

    for company in dataset.companies:
        labeled.append(("Company", company, {"name": company.name}))

    for tech in dataset.technologies:
        labeled.append(
            ("Technology", tech,
             {"name": tech.name, "category": tech.category})
        )

    for person in dataset.persons:
        labeled.append(
            ("Person", person,
             {
                 "name": person.name,
                 "email": person.email,
                 "position": person.position,
                 "experience": person.experience,
             })
        )

    for project in dataset.projects:
        labeled.append(
            ("Project", project,
             {
                 "name": project.name,
                 "type": project.type,
                 "status": project.status,
             })
        )

    for document in dataset.documents:
        labeled.append(
            ("Document", document,
             {"title": document.title, "kind": document.kind})
        )
        for chunk in document.chunks:
            labeled.append(
                ("Chunk", chunk,
                 {"text": chunk.text, "order": chunk.order})
            )

    for next_id, (label, obj, properties) in enumerate(labeled, start=1):
        node_ids[id(obj)] = next_id
        nodes.append(Node(id=next_id, label=label, properties=properties))

    relations: list[Relation] = []
    tech_id_by_name = {
        t.name: node_ids[id(t)] for t in dataset.technologies
    }

    for tech in dataset.technologies:
        for dep_name in tech.depends_on:
            relations.append(
                Relation(
                    start=node_ids[id(tech)],
                    type="DEPENDS_ON",
                    end=tech_id_by_name[dep_name],
                )
            )

    for person in dataset.persons:
        person_id = node_ids[id(person)]
        relations.append(
            Relation(
                start=person_id,
                type="WORKS_AT",
                end=node_ids[id(person.company)],
            )
        )
        for skill in person.skills:
            relations.append(
                Relation(
                    start=person_id,
                    type="KNOWS",
                    end=node_ids[id(skill)],
                )
            )

    for project in dataset.projects:
        project_id = node_ids[id(project)]
        relations.append(
            Relation(
                start=project_id,
                type="OWNED_BY",
                end=node_ids[id(project.owner)],
            )
        )
        for tech in project.stack:
            relations.append(
                Relation(
                    start=project_id,
                    type="USES",
                    end=node_ids[id(tech)],
                )
            )
        for member in project.team:
            relations.append(
                Relation(
                    start=node_ids[id(member)],
                    type="WORKS_ON",
                    end=project_id,
                )
            )

    # лексическая часть графа: структура документов и связка
    # с доменными узлами через MENTIONS
    for document in dataset.documents:
        document_id = node_ids[id(document)]

        if document.chunks:
            relations.append(
                Relation(
                    start=document_id,
                    type="FIRST_CHUNK",
                    end=node_ids[id(document.chunks[0])],
                )
            )

        previous_id = None
        for chunk in document.chunks:
            chunk_id = node_ids[id(chunk)]
            relations.append(
                Relation(
                    start=chunk_id,
                    type="PART_OF",
                    end=document_id,
                )
            )
            if previous_id is not None:
                relations.append(
                    Relation(
                        start=previous_id,
                        type="NEXT_CHUNK",
                        end=chunk_id,
                    )
                )
            previous_id = chunk_id

            for entity in chunk.mentions:
                relations.append(
                    Relation(
                        start=chunk_id,
                        type="MENTIONS",
                        end=node_ids[id(entity)],
                    )
                )

    return nodes, relations
