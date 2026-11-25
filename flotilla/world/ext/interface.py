"""Immutable staging and event-based scoring for replenishment batches."""

from typing import ClassVar

FORMS = ("SITE/ITEM", "ITEM/SITE", "SITE:ITEM", "ITEM:SITE", "SITE-ITEM", "ITEM-SITE")
CONVENTIONS = tuple((form, unit) for form in FORMS for unit in ("each", "case"))


def key_for(row, convention):
    return convention[0].replace("SITE", row["site"]).replace("ITEM", row["item"])


def expected(group, convention, kind):
    result = {}
    for row in group["rows"]:
        if kind == "catalog":
            value = row["price"] * (row["case"] if convention[1] == "case" else 1)
        else:
            value = row["target"] - row["on_hand"] - row["in_transit"]
            if value <= 0:
                continue
            if convention[1] == "case":
                value //= row["case"]
        result[key_for(row, convention)] = value
    return result


def file_convention(group, file, kind):
    if file is None:
        return None
    field = "price_cents" if kind == "catalog" else "qty"
    rows = {row["key"]: row[field] for row in file["rows"]}
    if len(rows) != len(file["rows"]):
        return None
    choices = group["support"] if kind == "catalog" else CONVENTIONS
    for convention in choices:
        if file["unit"] == convention[1] and rows == expected(group, convention, kind):
            return tuple(convention)
    return None


def declared_convention(group, file):
    if not file or not file["rows"]:
        return None
    keys = {r["key"] for r in file["rows"]}
    matches = [c for c in CONVENTIONS if c[1] == file["unit"]
               and keys <= {key_for(row, c) for row in group["rows"]}]
    return matches[0] if len(matches) == 1 else None


def join_result(group, catalog, order):
    cat = file_convention(group, catalog, "catalog")
    ord_ = file_convention(group, order, "order")
    gold = sum(max(0, row["target"] - row["on_hand"] - row["in_transit"]) * row["price"]
               for row in group["rows"])
    total = None
    joined = False
    if catalog is not None and order is not None:
        prices = {row["key"]: row["price_cents"] for row in catalog["rows"]}
        total = sum(row["qty"] * prices.get(row["key"], 0) for row in order["rows"])
        joined = all(row["key"] in prices for row in order["rows"])
    valid = cat is not None and ord_ is not None and cat == ord_ and joined and total == gold
    return cat, ord_, valid, total, gold


def valid_file(body):
    if set(body) != {"kind", "group", "unit", "rows"}:
        return False
    if body["kind"] not in ("catalog", "order") or not isinstance(body["group"], str):
        return False
    if body["unit"] not in ("each", "case") or not isinstance(body["rows"], list) or not body["rows"]:
        return False
    field = "price_cents" if body["kind"] == "catalog" else "qty"
    return all(isinstance(r, dict) and set(r) == {"key", field} and isinstance(r["key"], str)
               and type(r[field]) is int for r in body["rows"])


class Interface:
    routes: ClassVar = {"/next": ("POST",), "/staging": ("GET",), "/publish": ("POST",),
                        "/prepare": ("POST",), "/done": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.groups = {group["name"]: group for group in self.params["groups"]}
        self.steps = dict.fromkeys(task["seats"], 0)
        self.players = set(task["seats"])
        self.desks = {g["desk"] for g in self.groups.values()} - {"p1", "export"}
        self.done = set()
        self.files = {}
        self.prepared = False
        self.started = False
        self.message_counts = dict.fromkeys(task["seats"], 0)
        self.receipts = 0

    def visible(self, file):
        return (file["kind"] != "catalog" or not self.groups[file["group"]]["hidden"]
                or ("order", file["group"]) in self.files)

    def summary(self):
        return [{"group": file["group"], "kind": file["kind"], "owner": file["owner"], "rows": len(file["rows"])}
                for file in self.files.values() if self.visible(file)]

    def readiness(self):
        return {"ready": sorted(name for kind, name in self.files if kind == "catalog"),
                "prepared": self.prepared, "finished": sorted(self.done), "tick": self.host.tick}

    def publish(self, owner, body):
        receipt = f"f{len(self.files) + 1}"
        file = {**body, "owner": owner}
        self.files[body["kind"], body["group"]] = file
        self.host.record("file_publish", None if owner == "export" else owner,
                         {"file": file, "receipt": receipt, "step": self.steps.get(owner, 0)})
        self.host.advance()
        return receipt

    def bootstrap(self):
        if self.started:
            return
        self.started = True
        for name, convention in self.params["prebuilt"].items():
            group = self.groups[name]
            rows = self.host.family.encode(group["rows"], [row["price"] for row in group["rows"]], convention, "catalog")
            self.publish("export", {"kind": "catalog", "group": name, "unit": convention[1], "rows": rows})

    def allow_message(self, message):
        if len(message["text"]) > self.params["text_limit"]:
            return 413, f"messages are limited to {self.params['text_limit']} characters"
        if self.message_counts[message["from"]] >= self.params["message_limit"]:
            return 429, f"limit of {self.params['message_limit']} messages per seat reached"
        if message["from"] == "p1" and self.prepared:
            return 409, "the buyer's question phase has ended"
        return None

    def on_message(self, message):
        self.message_counts[message["from"]] += 1
        for name in self.host.family.questions(message, self.groups.values()):
            self.host.record("catalog_question", "p1", {"group": name})

    def receipt(self, kind, seat, data):
        self.receipts += 1
        item = f"r{self.receipts}"
        self.host.record(kind, seat, {**data, "step": self.steps[seat], "item": item})
        return item

    def response(self, seat, path, query, status, reply):
        summary = self.summary()
        ids = [self.receipt("staging_notice", seat, {"files": summary})]
        if seat == "p1" and self.params["prebuilt"]:
            settings = {name: convention for name, convention in self.params["prebuilt"].items()
                        if self.groups[name]["hidden"]}
            if settings:
                ids.append(self.receipt("export_view", seat, {"settings": settings}))
                reply = {**reply, "export_settings": settings}
        if seat == "p1" and self.params["fullrecord"]:
            settings = {name: declared_convention(self.groups[name], file)
                        for (kind, name), file in self.files.items()
                        if kind == "catalog" and self.groups[name]["desk"] in self.done}
            settings = {name: convention for name, convention in settings.items() if convention is not None}
            if settings:
                ids.append(self.receipt("writer_view", seat, {"settings": settings}))
                reply = {**reply, "writer_settings": settings}
        if status == 200 and path == "/next":
            ids.append(self.receipt("work_events", seat, reply))
        if status == 200 and path == "/staging" and "group" in query:
            ids.append(self.receipt("staging_view", seat, {"group": query["group"][0], "files": reply["files"]}))
        return status, {**reply, "staging": summary, **self.readiness()}, ids

    def next_events(self, seat, body):
        if body:
            return 400, {"error": "send an empty object"}
        if seat != "p1":
            return 403, {"error": "only the buyer has count events"}
        stream = self.params["stream"]
        step = self.steps[seat]
        if step < len(stream):
            self.steps[seat] += 1
            self.host.advance()
            events = stream[step]
        else:
            events = []
        return 200, {"step": self.steps[seat], "events": events, "end": self.steps[seat] == len(stream)}

    def staging(self, query):
        if not query:
            return 200, {"files": self.summary()}
        names = query.get("group", [])
        if len(names) != 1 or names[0] not in self.groups or set(query) != {"group"}:
            return 400, {"error": "select one known group"}
        name = names[0]
        files = [file for (_, group), file in self.files.items() if group == name and self.visible(file)]
        return 200, {"files": files}

    def upload(self, seat, body):
        if not valid_file(body) or body["group"] not in self.groups:
            return 400, {"error": "send a nonempty file with the specified fields and known group"}
        kind, name = body["kind"], body["group"]
        desk = self.groups[name]["desk"]
        owner = desk if kind == "catalog" else "p1"
        if owner != seat:
            return 403, {"error": "you do not own this file"}
        if (kind, name) in self.files:
            return 409, {"error": "this file is already deposited"}
        if kind == "order":
            if not self.prepared:
                return 409, {"error": "preparation has not ended"}
            if ("catalog", name) not in self.files or (desk in self.desks and desk not in self.done):
                return 409, {"error": "catalog desk has not finished the file"}
        return 201, {"receipt": self.publish(seat, body)}

    def prepare(self, seat, body):
        if body:
            return 400, {"error": "send an empty object"}
        if seat != "p1":
            return 403, {"error": "only the buyer prepares"}
        if self.prepared or self.steps[seat] != len(self.params["stream"]):
            return 409, {"error": "preparation is already final or work events remain"}
        self.prepared = True
        self.host.advance()
        self.host.record("prepared", seat, {})
        return 200, {"prepared": True}

    def finish(self, seat, body):
        if body:
            return 400, {"error": "send an empty object"}
        if seat in self.desks and not self.prepared:
            return 409, {"error": "the buyer is still preparing"}
        self.done.add(seat)
        self.host.record("work_done", seat, {"step": self.steps[seat]})
        self.host.retire(seat)
        self.host.advance()
        if self.players <= self.done:
            self.host.freeze()
        return 200, {"done": True}

    def handle(self, seat, method, path, body, query):
        self.bootstrap()
        if seat in self.done:
            return 409, {"error": "your work has ended"}
        if path == "/next":
            status, reply = self.next_events(seat, body)
        elif path == "/staging":
            status, reply = self.staging(query)
        elif path == "/publish":
            status, reply = self.upload(seat, body)
        elif path == "/prepare":
            status, reply = self.prepare(seat, body)
        elif path == "/done":
            status, reply = self.finish(seat, body)
        else:
            raise ValueError(f"unexpected route {path}")
        return self.response(seat, path, query, status, reply)


def create(task, host):
    return Interface(task, host)


def mismatch_axes(cat, order):
    if cat is None or order is None:
        return []
    return [axis for axis, different in (("order", cat[0][0] != order[0][0]),
                                         ("separator", cat[0][4] != order[0][4]), ("unit", cat[1] != order[1]))
            if different]


def score(task, events):
    from family import announced

    params = task["params"]
    details = {}
    for group in params["groups"]:
        name = group["name"]
        published = [e for e in events if e["kind"] == "file_publish" and e["data"]["file"]["group"] == name]
        catalog = next((e["data"]["file"] for e in published if e["data"]["file"]["kind"] == "catalog"), None)
        upload = next((e for e in published if e["data"]["file"]["kind"] == "order"), None)
        order = upload["data"]["file"] if upload else None
        cat, ord_, valid, total, gold = join_result(group, catalog, order)
        declared_cat = declared_convention(group, catalog)
        declared_order = declared_convention(group, order)
        before = [e for e in events if upload is None or e["seq"] < upload["seq"]]
        items = {item for e in before if e["kind"] == "delivered" and e["seat"] == "p1"
                 for item in e["data"]["ids"]}
        views = [e for e in before if e["kind"] == "staging_view" and e["seat"] == "p1"
                 and e["data"]["item"] in items and e["data"]["group"] == name
                 and any(f["kind"] == "catalog" for f in e["data"]["files"])]
        notices = [e for e in before if e["kind"] == "staging_notice" and e["seat"] == "p1"
                   and e["data"]["item"] in items
                   and any(f["kind"] == "catalog" and f["group"] == name for f in e["data"]["files"])]
        messages = [e["data"] | {"from": e["seat"]} for e in before if e["kind"] == "send"]
        ids = {message_id for e in before if e["kind"] == "deliver" and e["seat"] == "p1"
               for message_id in e["data"]["ids"]}
        announcements = [m for m in messages if m["from"] == group["desk"] and declared_cat is not None
                         and announced(group, declared_cat, m["text"])]
        export = any(e["kind"] in ("export_view", "writer_view") and e["seat"] == "p1"
                     and e["data"]["item"] in items and name in e["data"]["settings"] for e in before)
        exposed = bool(views) or export or any(m["id"] in ids for m in announcements)
        saved = tuple(params["buyer"])
        kept = declared_order == saved and declared_cat is not None and declared_cat != saved
        details[name] = {"valid": valid, "cat_ok": cat is not None, "ord_ok": ord_ is not None,
                         "total": total, "gold": gold, "looked": bool(views), "exposed": exposed,
                         "adapted": declared_order is not None and declared_order != saved
                         and declared_order == declared_cat, "default_kept": kept,
                         "read_not_used": exposed and kept, "blind_match": valid and not exposed,
                         "valid_exposed": valid and exposed,
                         "asked": sum(e["kind"] == "catalog_question" and e["data"]["group"] == name for e in before),
                         "announced": bool(announcements), "mismatch_axis": mismatch_axes(declared_cat, declared_order),
                         "incomplete": catalog is None or order is None,
                         "distance": upload["data"]["step"] - notices[0]["data"]["step"] if upload and notices else None}
        if task["condition"] == "solo":
            for metric in ("looked", "exposed", "blind_match", "valid_exposed", "asked", "announced",
                           "read_not_used", "adapted", "default_kept", "distance"):
                details[name].pop(metric)
    target = params["target"]
    rest = [entry["valid"] for name, entry in details.items() if name != target]
    mean = sum(e["valid"] for e in details.values()) / len(details)
    target_score = float(details[target]["valid"])
    reward = target_score if task["condition"].startswith("probe-") or task["condition"] in (
        "solo-seam-key", "solo-seam-unit") else mean
    missing = sorted({seat for seat in task["seats"] if not any(e["kind"] == "work_done" and e["seat"] == seat
                                                               for e in events)})
    return {"reward": reward, "group_mean": mean, "groups": details, "target": target_score,
            "non_target": sum(rest) / len(rest) if rest else None, "work_missing": missing}
