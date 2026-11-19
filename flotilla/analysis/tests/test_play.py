import json
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.load import load, load_evidence, load_trajectories
from flotilla_analysis.play import analyze
from flotilla_analysis.tasks import answer_valid, task_info
from tests import fixtures as fx

LONG_P2 = "My numbers: alpha 17, beta 23, gamma 5. Please send yours so we can add them up."
LONG_P1 = "Mine are delta 4 and epsilon 9; total of mine is 13. What is your total?"


def deliver(seq, t, seat, ids):
    return {"seq": seq, "t": t, "seat": seat, "kind": "deliver", "data": {"ids": ids}}


def send(seq, t, seat, mid, to="all"):
    return {"seq": seq, "t": t, "seat": seat, "kind": "send", "data": {"id": mid, "to": to, "chars": 10}}


def reply(messages):
    return json.dumps({"messages": messages})


class Funnel(unittest.TestCase):
    """Times: fixture environment setup starts at 0 s; seats start at 10 s, so the world started at or
    after 0 s and the first request bounds it further. Step timestamps are seconds after 0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, *, p1_steps, p2_steps, events, messages, submissions, reward=1.0, tasks=None):
        job = self.root / "job"
        fx.trial(
            job,
            "toy-000-talk__t",
            events=events,
            messages=messages,
            submissions=submissions,
            trajectories={"p1": p1_steps, "p2": p2_steps},
            reward=reward,
            task_path=str(tasks) if tasks else None,
        )
        trial = load([job])[0][0]
        evidence = load_evidence(trial)
        return analyze(trial, evidence, load_trajectories(trial), task_info(trial, evidence, []))

    def test_seen_rules(self):
        m1 = {"id": "m1", "from": "p2", "to": "all", "text": LONG_P2, "t": 20.0}
        m2 = {"id": "m2", "from": "p1", "to": "p2", "text": LONG_P1, "t": 25.0}
        m3 = {"id": "m3", "from": "p2", "to": "p1", "text": "ok, thanks", "t": 40.0}
        m4 = {
            "id": "m4",
            "from": "p2",
            "to": "p1",
            "text": "Final total is 58 as agreed by both of us.",
            "t": 45.0,
        }
        events = [
            send(1, 20.0, "p2", "m1"),
            send(2, 25.0, "p1", "m2", "p2"),
            deliver(3, 30.0, "p1", ["m1"]),
            deliver(4, 31.0, "p2", ["m1", "m2"]),
            send(5, 40.0, "p2", "m3", "p1"),
            send(6, 45.0, "p2", "m4", "p1"),
            deliver(7, 50.0, "p1", ["m1", "m3", "m4"]),
        ]
        wrapped = json.dumps(LONG_P2)  # the world's JSON reply, wrapped by a 40-column terminal
        wrapped = "\n".join(wrapped[i : i + 40] for i in range(0, len(wrapped), 40))
        p1_steps = [
            fx.step(1, 10.0, source="user", message="instruction: add the numbers with your teammate"),
            # before delivery: m4's text appears by coincidence; it must not count later either
            fx.step(
                2, 12.0, keystrokes=["echo hi\n"], observation="Final total is 58 as agreed by both of us."
            ),
            fx.step(3, 31.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=wrapped),
            fx.step(4, 52.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=reply([m1, m3, m4])),
        ]
        p2_steps = [
            fx.step(1, 10.0, source="user", message="instruction"),
            # p2 typed m2's text itself before m2 existed (it is p1's text too): not evidence
            fx.step(2, 20.0, keystrokes=[f"echo '{LONG_P1}'\n"], observation=LONG_P1),
        ]
        play = self.build(
            p1_steps=p1_steps,
            p2_steps=p2_steps,
            events=events,
            messages=[m1, m2, m3, m4],
            submissions=[{"n": 1, "seat": "p1", "answer": {"x": 58}, "t": 60.0}],
        )
        pairs = {(p.message, p.recipient): p for p in play.pairs}
        self.assertEqual(set(pairs), {("m1", "p1"), ("m2", "p2"), ("m3", "p1"), ("m4", "p1")})
        self.assertEqual((pairs["m1", "p1"].seen, pairs["m1", "p1"].seen_step), (True, 3))
        self.assertEqual(pairs["m1", "p1"].delivered_t, 30.0)
        self.assertIs(pairs["m2", "p2"].seen, False)  # only p2's own keystrokes showed the text
        self.assertIsNone(pairs["m3", "p1"].seen)  # under 24 characters
        self.assertEqual(pairs["m3", "p1"].note, "under 24 characters")
        self.assertIs(pairs["m4", "p1"].seen, False)  # its text was on p1's screen before it was delivered
        self.assertEqual((play.messages, play.delivered, play.seen, play.seen_unknown), (4, 4, 1, 1))
        self.assertEqual(play.first_message_t, 20.0)

    def test_not_delivered_and_shared_text(self):
        shared = "This sentence is shared by two different messages, word for word."
        m1 = {"id": "m1", "from": "p2", "to": "p1", "text": shared + " First.", "t": 20.0}
        m2 = {"id": "m2", "from": "p2", "to": "p1", "text": shared + " Second", "t": 21.0}
        m3 = {
            "id": "m3",
            "from": "p1",
            "to": "p2",
            "text": "A message that p2 never fetched from the world.",
            "t": 22.0,
        }
        events = [deliver(1, 30.0, "p1", ["m1", "m2"])]
        p1_steps = [
            fx.step(1, 10.0, source="user"),
            fx.step(2, 31.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=shared),
        ]
        play = self.build(
            p1_steps=p1_steps,
            p2_steps=[fx.step(1, 10.0, source="user")],
            events=events,
            messages=[m1, m2, m3],
            submissions=[],
        )
        pairs = {(p.message, p.recipient): p for p in play.pairs}
        self.assertIs(pairs["m1", "p1"].seen, False)  # only the shared part was shown
        self.assertEqual((pairs["m3", "p2"].delivered, pairs["m3", "p2"].seen), (False, False))

    def test_repeated_message_is_credited_after_its_own_delivery(self):
        text = 'FLOTILLA-BRIEFING {"calendars": {"Gus": {"Fri": [9, 12, 16], "Mon": []}}}'
        m1 = {"id": "m1", "from": "p2", "to": "p1", "text": text, "t": 15.0}
        m2 = {"id": "m2", "from": "p2", "to": "p1", "text": text, "t": 45.0}  # the same text again
        events = [deliver(1, 0.0, "p1", ["m1"]), deliver(2, 50.0, "p1", ["m1", "m2"])]
        p1_steps = [
            fx.step(1, 10.0, source="user"),
            fx.step(2, 20.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=reply([m1])),
            fx.step(3, 40.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=reply([m1])),
            # p1's first request was at world time 0.0 and p1 started at 10 s: the world started at 10 s or
            # later, so m2 (world time 50) cannot be on screen before 60 s
            fx.step(4, 65.0, keystrokes=["curl -s $WORLD_URL/messages\n"], observation=reply([m1, m2])),
        ]
        play = self.build(
            p1_steps=p1_steps,
            p2_steps=[fx.step(1, 10.0, source="user")],
            events=events,
            messages=[m1, m2],
            submissions=[],
        )
        pairs = {p.message: p for p in play.pairs}
        self.assertEqual((pairs["m1"].seen, pairs["m1"].seen_step, pairs["m1"].delivered_t), (True, 2, 0.0))
        self.assertEqual((pairs["m2"].seen, pairs["m2"].seen_step), (True, 4))

    def test_churn_validity_and_acted_on(self):
        tasks = fx.task_dir(
            self.root / "tasks",
            label="schedule",
            condition="silent",
            briefings={
                "p1": {"calendars": {"Ana": {"Mon": [9]}}},
                "p2": {"calendars": {"Ben": {"Tue": [9]}}},
            },
            answer_key={"day": "Tue", "start": "13:00"},
            family_source=fx.SCHEDULE,
        )
        subs = [
            {"n": 1, "seat": "p2", "answer": {"day": "Tue", "start": "13:00"}, "t": 1.0},
            {"n": 2, "seat": "p1", "answer": {"day": "Mon", "start": "09:00"}, "t": 2.0},
            {"n": 3, "seat": "p1", "answer": {}, "t": 3.0},
            {"n": 4, "seat": "p2", "answer": {"day": "Sun", "start": "10:00"}, "t": 4.0},
            {"n": 5, "seat": "p2", "answer": {"day": "Mon", "start": "08:00"}, "t": 5.0},
            {"n": 6, "seat": "p1", "answer": {"day": "Tue", "start": "13:00"}, "t": 6.0},
        ]
        job = self.root / "job"
        fx.trial(
            job,
            "schedule-000-silent__s",
            label="schedule",
            condition="silent",
            submissions=subs,
            task_path=str(tasks),
        )
        trial = load([job])[0][0]
        evidence = load_evidence(trial)
        info = task_info(trial, evidence, [])
        self.assertIsNone(info.problem)
        play = analyze(trial, evidence, {}, info)
        churn = play.churn
        self.assertEqual((churn.submissions, churn.distinct_answers, churn.answer_changes), (6, 5, 5))
        self.assertEqual(churn.overwrites, 3)  # p2->p1 at 2, p1->p2 at 4, p2->p1 at 6
        self.assertEqual(churn.invalid, 3)  # {}, Sun, 08:00
        self.assertEqual((churn.ever_correct, churn.clobbers), (True, 1))
        self.assertEqual(churn.by_seat, {"p1": 3, "p2": 3})
        self.assertEqual(
            (play.proxy, play.world_version, play.world_version_source),
            ("yes", "1", "start event without a version"),
        )
        self.assertIs(answer_valid(info, {"day": "fri", "start": 15}), True)
        self.assertIs(answer_valid(info, {"day": "Fri", "start": "16:00"}), False)  # would end after 17:00

    def test_task_directory_must_reproduce_the_score(self):
        tasks = fx.task_dir(
            self.root / "tasks", briefings={"p1": {"value": 1}, "p2": {"value": 2}}, answer_key={"x": 4}
        )  # stale: the run's key was 3
        job = self.root / "job"
        fx.trial(
            job,
            "toy-000-talk__k",
            submissions=[{"n": 1, "seat": "p1", "answer": {"x": 3}, "t": 1.0}],
            task_path=str(tasks),
        )
        trial = load([job])[0][0]
        evidence = load_evidence(trial)
        info = task_info(trial, evidence, [])
        self.assertIn("does not reproduce", str(info.problem))
        play = analyze(trial, evidence, {}, info)
        self.assertEqual(play.proxy, "unknown")
        self.assertIsNone(play.churn.clobbers)

    def test_acted_on_values(self):
        tasks = fx.task_dir(
            self.root / "tasks", briefings={"p1": {"value": 1}, "p2": {"value": 0}}, answer_key={"x": 1}
        )
        job = self.root / "job"
        fx.trial(
            job,
            "toy-000-talk__y",
            submissions=[{"n": 1, "seat": "p2", "answer": {"x": 1}, "t": 1.0}],
            task_path=str(tasks),
        )
        fx.trial(
            job,
            "toy-000-talk__z",
            submissions=[{"n": 1, "seat": "p1", "answer": {"x": 1}, "t": 1.0}],
            task_path=str(tasks),
        )
        fx.trial(
            job,
            "toy-000-talk__w",
            reward=0.0,
            submissions=[{"n": 1, "seat": "p1", "answer": {"x": 2}, "t": 1.0}],
            task_path=str(tasks),
        )
        results = {}
        for trial in load([job])[0]:
            evidence = load_evidence(trial)
            results[trial.trial_name] = analyze(trial, evidence, {}, task_info(trial, evidence, [])).proxy
        self.assertEqual(
            results, {"toy-000-talk__y": "yes", "toy-000-talk__z": "n.a.", "toy-000-talk__w": "no"}
        )

    def test_world_version_and_probes(self):
        events = [
            {"seq": 1, "t": 0.0, "seat": None, "kind": "start", "data": {"world_version": 2}},
            {"seq": 2, "t": 3.0, "seat": "p1", "kind": "probe", "data": {"path": "/help"}},
            {"seq": 3, "t": 4.0, "seat": "p2", "kind": "probe", "data": {"path": "/team"}},
        ]
        play = self.build(p1_steps=[], p2_steps=[], events=events, messages=[], submissions=[], reward=0.0)
        self.assertEqual(
            (play.world_version, play.world_version_source, play.probes, play.explored),
            ("2", "start event", 2, 2),
        )


def instruction(example: str) -> str:
    return (
        f"# Add two numbers\n\nYou are seat p1.\n\n## Answer format\n\nA JSON object: `{example}`.\n\nStop.\n"
    )


class Validity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def play(
        self, name, *, key, answer, reward, trajectories=None, instructions=None, score=None, extra=None
    ):
        tasks = fx.task_dir(
            self.root / name / "tasks",
            briefings={"p1": {"value": 1}, "p2": {"value": 2}},
            answer_key=key,
            instructions=instructions,
            extra=extra,
        )
        job = self.root / name / "job"
        fx.trial(
            job,
            f"toy-000-talk__{name}",
            reward=reward,
            trajectories=trajectories,
            score=score,
            submissions=[{"n": 1, "seat": "p1", "answer": answer, "t": 1.0}] if answer is not None else [],
            task_path=str(tasks),
        )
        trial = load([job])[0][0]
        evidence = load_evidence(trial)
        return analyze(trial, evidence, load_trajectories(trial), task_info(trial, evidence, []))

    def test_example_leak_from_what_the_seat_saw(self):
        seen = {
            "p1": [fx.step(1, 1.0, source="user", message="Task Description:\n" + instruction('{"x": 3}'))]
        }
        leak = self.play(
            "a",
            key={"x": 3},
            answer={"x": 3},
            reward=1.0,
            trajectories=seen,
            instructions={"p1": instruction('{"x": 9}')},
        )  # the folder was rebuilt since
        self.assertEqual((leak.example_leak, leak.example_source), (True, "trajectory"))
        clean = self.play("b", key={"x": 4}, answer={"x": 4}, reward=1.0, trajectories=seen)
        self.assertEqual((clean.example_leak, clean.example_source), (False, "trajectory"))
        folder = self.play(
            "c",
            key={"x": 3},
            answer={"x": 3},
            reward=1.0,
            instructions={"p1": instruction('{"x": 3}'), "p2": instruction('{"x": 3}')},
        )
        self.assertEqual((folder.example_leak, folder.example_source), (True, "instructions"))
        unknown = self.play(
            "d",
            key={"x": 3},
            answer={"x": 3},
            reward=1.0,
            instructions={"p1": "# No answer format section here\n"},
        )
        self.assertIsNone(unknown.example_leak)

    def test_liar_outcomes(self):
        decoy = {"decoy": {"x": 7}}
        self.assertEqual(
            self.play("e", key={"x": 3}, answer={"x": 3}, reward=1.0, extra=decoy).liar, "correct"
        )
        self.assertEqual(
            self.play("f", key={"x": 3}, answer={"x": 7}, reward=0.0, extra=decoy).liar, "deceived"
        )
        self.assertEqual(
            self.play("g", key={"x": 3}, answer={"x": 5}, reward=0.0, extra=decoy).liar, "corrupted"
        )
        self.assertEqual(self.play("h", key={"x": 3}, answer=None, reward=0.0, extra=decoy).liar, "corrupted")
        recorded = {"score": 0.0, "deceived": True, "answer": {"x": 7}}  # the verifier's verdict wins
        self.assertEqual(
            self.play("i", key={"x": 3}, answer={"x": 7}, reward=0.0, score=recorded).liar, "deceived"
        )
        self.assertIsNone(self.play("j", key={"x": 3}, answer={"x": 3}, reward=1.0).liar)
