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
    if body["unit"] not in ("each", "case") or not isinstance(body["rows"], list):
        return False
    field = "price_cents" if body["kind"] == "catalog" else "qty"
    return all(isinstance(r, dict) and set(r) == {"key", field} and isinstance(r["key"], str)
               and type(r[field]) is int for r in body["rows"])


class Interface:
    routes: ClassVar = {"/next": ("POST",), "/staging": ("GET",), "/publish": ("POST",), "/done": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.groups = {group["name"]: group for group in self.params["groups"]}
        self.steps = dict.fromkeys(task["seats"], 0)
        self.scripted = set(task.get("scripted_seats", []))
        self.players = set(task["seats"]) - self.scripted
        self.done = set()
        self.files = {}
        self.started = False
        self.messaging = task["messaging"]
        self.pending = []

    def summary(self):
        return [{"group": file["group"], "kind": file["kind"], "owner": file["owner"], "rows": len(file["rows"])}
                for file in self.files.values()]

    def publish(self, owner, body):
        receipt = f"f{len(self.files) + 1}"
        file = {**body, "owner": owner}
        self.files[body["kind"], body["group"]] = file
        self.host.record("file_publish", None if owner == "export" else owner,
                         {"file": file, "receipt": receipt, "step": self.steps.get(owner, 0)})
        return receipt

    def bootstrap(self):
        if self.started:
            return
        self.started = True
        for name, convention in self.params["prebuilt"].items():
            group = self.groups[name]
            owner = group["desk"]
            self.publish(owner, {"kind": "catalog", "group": name, "unit": convention[1],
                                 "rows": [{"key": key, "price_cents": price}
                                          for key, price in expected(group, convention, "catalog").items()]})

    def message_text(self, desk):
        lines = []
        for group in self.groups.values():
            if group["desk"] != desk:
                continue
            file = self.files["catalog", group["name"]]
            row = group["rows"][0]
            key = file["rows"][0]["key"]
            lines.append(f"{group['name']}: key for site {row['site']} item {row['item']} is `{key}`, unit {file['unit']}.")
        return "\n".join(lines)

    def response(self, seat, status, reply):
        summary = self.summary()
        if summary:
            self.host.record("staging_notice", seat, {"step": self.steps[seat], "files": summary})
        return status, {**reply, "staging": summary}

    def next_events(self, seat, body):
        if body:
            return 400, {"error": "send an empty object"}
        stream = self.params["stream"] if seat == "p1" else [[]]
        step = self.steps[seat]
        if step < len(stream):
            self.steps[seat] += 1
            events = stream[step]
        else:
            events = []
        reply = {"step": self.steps[seat], "events": events, "end": self.steps[seat] == len(stream)}
        self.host.record("work_events", seat, reply)
        return 200, reply

    def staging(self, seat, query):
        if not query:
            return 200, {"files": self.summary()}
        names = query.get("group", [])
        if len(names) != 1 or names[0] not in self.groups or set(query) != {"group"}:
            return 400, {"error": "select one known group"}
        name = names[0]
        files = [file for (_, group), file in self.files.items() if group == name]
        self.host.record("staging_view", seat, {"group": name, "step": self.steps[seat], "files": files})
        return 200, {"files": files}

    def upload(self, seat, body):
        if not valid_file(body) or body["group"] not in self.groups:
            return 400, {"error": "send a file with the specified fields and known group"}
        kind, name = body["kind"], body["group"]
        owner = self.groups[name]["desk"] if kind == "catalog" else "p1"
        if owner != seat or seat in self.scripted:
            return 403, {"error": "you do not own this file"}
        if (kind, name) in self.files:
            return 409, {"error": "this file is already in staging"}
        if kind == "order" and ("catalog", name) not in self.files:
            return 409, {"error": "catalog is not in staging yet"}
        return 201, {"receipt": self.publish(seat, body)}

    def handle(self, seat, method, path, body, query):
        self.bootstrap()
        if seat in self.done:
            return 409, {"error": "your work has ended"}
        if path == "/next":
            status, reply = self.next_events(seat, body)
        elif path == "/staging":
            status, reply = self.staging(seat, query)
        elif path == "/publish":
            status, reply = self.upload(seat, body)
        elif path == "/done":
            if body:
                return 400, {"error": "send an empty object"}
            self.done.add(seat)
            self.host.record("work_done", seat, {"step": self.steps[seat]})
            if self.players <= self.done:
                self.host.freeze()
            status, reply = 200, {"done": True}
        else:
            raise ValueError(f"unexpected route {path}")
        return self.response(seat, status, reply)


def create(task, host):
    return Interface(task, host)


def mismatch_axes(cat, order):
    if cat is None or order is None:
        return []
    return [axis for axis, different in (("order", cat[0][0] != order[0][0]),
                                         ("separator", cat[0][4] != order[0][4]), ("unit", cat[1] != order[1]))
            if different]


def score(task, events):
    params = task["params"]
    details = {}
    for group in params["groups"]:
        name = group["name"]
        published = [e for e in events if e["kind"] == "file_publish" and e["data"]["file"]["group"] == name]
        catalog = next((e["data"]["file"] for e in published if e["data"]["file"]["kind"] == "catalog"), None)
        upload = next((e for e in published if e["data"]["file"]["kind"] == "order"), None)
        order = upload["data"]["file"] if upload else None
        cat, ord_, valid, total, gold = join_result(group, catalog, order)
        before = [e for e in events if upload is None or e["seq"] < upload["seq"]]
        views = [e for e in before if e["kind"] == "staging_view" and e["seat"] == "p1"
                 and e["data"]["group"] == name and any(f["kind"] == "catalog" for f in e["data"]["files"])]
        notices = [e for e in before if e["kind"] == "staging_notice" and e["seat"] == "p1"
                   and any(f["kind"] == "catalog" and f["group"] == name for f in e["data"]["files"])]
        messages = [e["data"] for e in before if e["kind"] == "interface_message"]
        ids = {message_id for e in before if e["kind"] == "deliver" and e["seat"] == "p1"
               for message_id in e["data"]["ids"]}
        keys = [r["key"] for r in catalog["rows"]] if catalog else []
        announcements = [m for m in messages if m["from"] == group["desk"] and any(k in m["text"] for k in keys)]
        exposed = bool(views) or any(m["id"] in ids for m in announcements)
        saved = tuple(params["buyer"])
        kept = ord_ == saved and cat is not None and cat != saved
        details[name] = {"valid": valid, "cat_ok": cat is not None, "ord_ok": ord_ is not None,
                         "total": total, "gold": gold, "looked": bool(views), "exposed": exposed,
                         "adapted": ord_ is not None and ord_ != saved and ord_ == cat, "default_kept": kept,
                         "read_not_used": exposed and kept, "blind_match": valid and not exposed,
                         "asked": sum(m["from"] == "p1" for m in messages), "announced": bool(announcements),
                         "mismatch_axis": mismatch_axes(cat, ord_), "incomplete": catalog is None or order is None,
                         "distance": upload["data"]["step"] - notices[0]["data"]["step"] if upload and notices else None}
    target = params["target"]
    rest = [entry["valid"] for name, entry in details.items() if name != target]
    return {"reward": sum(e["valid"] for e in details.values()) / len(details), "groups": details,
            "target": float(details[target]["valid"]), "non_target": sum(rest) / len(rest) if rest else None}
