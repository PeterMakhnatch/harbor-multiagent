from .protocol import Document


def select_registered_subset(
    pack_records: list[Document], enrolled: list[Document], *, task: str
) -> dict[str, Document]:
    registered: dict[str, Document] = {}
    for record in enrolled:
        record_id = record["id"]
        if record["task"] != task or record_id in registered:
            raise ValueError(f"invalid {task} source enrollment: {record_id}")
        registered[record_id] = record

    selected: dict[str, Document] = {}
    for record in pack_records:
        record_id = record["id"]
        original = registered.get(record_id)
        if original is None and record["task"] != task:
            continue
        if original is None:
            raise ValueError(f"unregistered {task} source: {record_id}")
        if record_id in selected:
            raise ValueError(f"duplicate {task} source: {record_id}")
        if record.keys() != original.keys() or any(
            value != original[key] for key, value in record.items() if key != "peer"
        ):
            raise ValueError(f"{task} source differs from enrollment: {record_id}")
        selected[record_id] = original
    return selected
