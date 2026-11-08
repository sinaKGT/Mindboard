"""Planned answers (DESIGN.md 47): the expert plans a question, runs each step
against its boards - each step seeing what the earlier ones found - and answers
from the findings. Simple questions are left alone, and one Stop ends the run."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from mindboard import ask, experts
from mindboard.app import create_app
from mindboard.boards import conn_for, create_board
from mindboard.ids import ulid
from mindboard.ops import apply_envelopes
from mindboard.search_index import BoardIndex


def node(title, text):
    return {"id": ulid(), "type": "text", "pos": [0, 0], "title": title, "content": {"text": text}}


@pytest.fixture()
def rag(dd):
    b = create_board(dd, "RAG notes", "")
    cards = {t: node(t, x) for t, x in [
        ("Naive RAG", "chunk, embed, retrieve the top k, generate - simple and fast"),
        ("Re-ranking", "a cross-encoder reads the question with each retrieved chunk"),
        ("Graph RAG", "builds a knowledge graph; good for multi-hop questions, costly to build"),
        ("Lecture slides", "slides are short and full of terms; keyword search helps"),
    ]}
    assert not apply_envelopes(conn_for(dd, b["id"]), b["id"],
                               [{"opId": ulid(), "op": {"kind": "node.create", "node": n}}
                                for n in cards.values()])["rejected"]
    e = experts.create(dd, {"name": "Retrieval Engineer", "boards": [b["id"]]})
    yield {"expert": e, "board": b["id"], **{t: n["id"] for t, n in cards.items()}}
    BoardIndex.drop(dd, b["id"])


class Scripted:
    """The local model as a run sees it: one slot, a Stop, and replies said
    in turn - the plan first, then each step, then the answer."""

    def __init__(self, *replies, stop_after=None):
        from types import SimpleNamespace
        self.replies, self.asked, self.stop_after = list(replies), [], stop_after
        self._asking, self._cancel = threading.Lock(), threading.Event()
        self.inst = SimpleNamespace(model="m")

    def running(self):
        return True

    def stop(self):
        pass

    def memory_note(self):
        return None

    def cancel(self):
        self._cancel.set()

    def cancelled(self):
        return self._cancel.is_set()

    def stream(self, messages, *, hold=True, **kw):
        assert not hold or not self._asking.locked(), "a held run never takes the slot again"
        self.asked.append(messages)
        if self._cancel.is_set():
            yield {"type": "done", "stopped": True}
            return
        reply = self.replies.pop(0) if self.replies else "Fine.\nNEXT: a\nNEXT: b\nNEXT: c"
        yield {"type": "text", "text": reply}
        if self.stop_after is not None and len(self.asked) >= self.stop_after:
            self._cancel.set()
        yield {"type": "done", "stopped": self._cancel.is_set()}


def plan_reply(steps, kind="design"):
    return "Here is the plan: " + json.dumps({
        "kind": kind, "reading": "You want students to ask about their lectures.",
        "weigh": ["who asks - students", "material - lecture slides"], "steps": steps})


def run(dd, rag, model, question="Which RAG architecture for students asking about lectures?"):
    lib = experts.library(dd, rag["expert"])
    return list(experts.answer(model, dd, rag["expert"], lib, question, []))


def test_a_plan_is_cleaned_to_what_can_be_run():
    got = ask.clean_plan({"kind": "Design", "weigh": ["a", " ", "b"], "steps": [
        {"find": "naive rag", "work_out": "Is it enough?"},
        {"find": "  graph   rag ", "work_out": "", "uses": 7},
        "not a step", {"find": "", "work_out": ""},
        {"find": "", "work_out": "Weigh them.", "uses": 1},
        {"find": "x", "work_out": "y"}, {"find": "too", "work_out": "many"}]})
    assert got["kind"] == "design" and got["weigh"] == ["a", "b"]
    assert [s["find"] for s in got["steps"]] == ["naive rag", "graph rag", "", "x"], "at most four"
    assert got["steps"][1] == {"find": "graph rag", "work_out": "What the cards say about graph rag.",
                               "uses": 0}, "a step cannot use one that comes after it"
    assert got["steps"][2]["uses"] == 1


def test_a_simple_question_gets_no_steps():
    one = ask.clean_plan({"kind": "fact", "steps": [{"find": "reranking", "work_out": "What is it?"}]})
    assert one["steps"] == [], "one look is the question's own search (DESIGN.md 37)"
    only_thinking = ask.clean_plan({"kind": "advice", "steps": [{"find": "", "work_out": "a"},
                                                                {"find": "", "work_out": "b"}]})
    assert only_thinking["steps"] == [], "steps that look nothing up add nothing"
    assert ask.clean_plan({"kind": "explain"})["weigh"] == [], "only weighed kinds weigh"


def test_a_plan_that_cannot_be_read_answers_the_question_as_it_stands(dd, rag):
    model = Scripted("I would rather not plan.", "Start with Naive RAG [[" + rag["Naive RAG"] + "]].")
    evs = run(dd, rag, model)
    assert [e["event"] for e in evs if e["event"] in ("plan", "step")] == []
    assert evs[-1]["event"] == "done" and evs[-1]["cited"] == [rag["Naive RAG"]]


def test_each_step_runs_in_turn_and_the_answer_builds_on_them(dd, rag):
    model = Scripted(
        plan_reply([{"find": "naive rag re-ranking", "work_out": "Does Naive RAG with re-ranking fit?"},
                    {"find": "graph rag", "work_out": "Does Graph RAG fit?"},
                    {"find": "", "work_out": "Weigh them for students.", "uses": 1}]),
        f"Simple and fast [[{rag['Naive RAG']}]]; a cross-encoder sharpens it [[{rag['Re-ranking']}]].",
        f"Costly to build [[{rag['Graph RAG']}]], and an invented one [[{'Z' * 26}]].",
        f"Naive RAG with re-ranking suits short slides [[{rag['Naive RAG']}]].",
        f"Start with Naive RAG and re-ranking [[{rag['Re-ranking']}]].\nNEXT: a\nNEXT: b\nNEXT: c")
    evs = run(dd, rag, model)
    names = [e["event"] for e in evs]
    assert names[:7] == ["plan", "step", "step", "step", "step", "step", "step"]
    plan = evs[0]
    assert len(plan["steps"]) == 3 and plan["reading"].startswith("You want")
    done = [e for e in evs if e["event"] == "step" and e["status"] == "done"]
    assert [e["i"] for e in done] == [0, 1, 2]
    assert done[0]["cards"] == [rag["Naive RAG"], rag["Re-ranking"]]
    assert done[0]["where"][rag["Naive RAG"]]["title"] == "Naive RAG", "a finding's citations open"
    assert done[1]["cards"] == [rag["Graph RAG"]] and "Z" * 26 not in done[1]["text"], "invented ids dropped"
    assert done[2]["looked_at"] == 0, "the weighing step looks nothing up"

    step3 = model.asked[3][1]["content"]
    assert "WHAT EARLIER STEPS FOUND" in step3 and "Costly to build" in step3, "a step sees the earlier ones"
    assert "STEP 3\nWeigh them for students." in step3

    answer_prompt = model.asked[4][-1]["content"]
    assert "WHAT YOUR STEPS FOUND" in answer_prompt and "suits short slides" in answer_prompt
    assert "What decides the answer:\n- who asks - students" in answer_prompt
    context = next(e for e in evs if e["event"] == "context")
    for c in (rag["Naive RAG"], rag["Re-ranking"], rag["Graph RAG"]):
        assert c in context["cards"], "what the steps cited can be cited in the answer"
    assert evs[-1]["event"] == "done" and evs[-1]["cited"] == [rag["Re-ranking"]]
    assert evs[-1]["nexts"] == ["a", "b", "c"]


def test_a_link_in_a_chain_is_looked_for_by_what_the_earlier_step_found(dd, rag, monkeypatch):
    searched = []
    real = experts.search
    monkeypatch.setattr(experts, "search", lambda lib, q, *a, **k: searched.append(q) or real(lib, q, *a, **k))
    model = Scripted(
        plan_reply([{"find": "multi-hop questions", "work_out": "Which approach handles multi-hop?"},
                    {"find": "cost", "work_out": "What does it cost to build?", "uses": 1}], kind="explain"),
        f"Graph RAG [[{rag['Graph RAG']}]].", "Costly.", "Graph RAG, at a cost [[" + rag["Graph RAG"] + "]].")
    run(dd, rag, model, "What handles multi-hop questions, and what does it cost?")
    assert searched[0] == "What handles multi-hop questions, and what does it cost?", \
        "a glimpse of the notes first, for the planner"
    assert "WHAT THE NOTES HOLD" in model.asked[0][1]["content"] and "- Graph RAG" in model.asked[0][1]["content"]
    assert searched[1] == "multi-hop questions"
    assert searched[2] == "cost Graph RAG", "the second step searches by the first step's card"


def test_a_stop_ends_the_run_between_steps(dd, rag):
    model = Scripted(plan_reply([{"find": "naive rag", "work_out": "a"}, {"find": "graph rag", "work_out": "b"}]),
                     "first finding", stop_after=2)
    evs = run(dd, rag, model)
    steps = [(e["i"], e["status"]) for e in evs if e["event"] == "step"]
    assert steps == [(0, "running"), (0, "stopped")], "the second step never starts"
    assert evs[-1]["event"] == "done" and evs[-1]["stopped"] and evs[-1]["answer"] == ""
    assert len(model.asked) == 2, "nothing is asked after the Stop"


def test_the_endpoint_streams_the_plan_and_holds_the_model(dd, rag, monkeypatch):
    app = create_app(dd, "t")
    model = Scripted(
        plan_reply([{"find": "naive rag", "work_out": "a"}, {"find": "graph rag", "work_out": "b"}]),
        "one", "two", f"Answer [[{rag['Naive RAG']}]].\nNEXT: a\nNEXT: b\nNEXT: c")
    seen_held = []
    real_stream = model.stream

    def watched(messages, **kw):
        seen_held.append(model._asking.locked())
        yield from real_stream(messages, **kw)

    model.stream = watched
    from mindboard import llm
    monkeypatch.setattr(llm, "find_install", lambda home=None: type("I", (), {"model": "m", "embedder": None,
                                                                         "reranker": None})())
    with TestClient(app) as c:
        app.state.ask["model"] = model
        res = c.post(f"/api/experts/{rag['expert']['id']}/ask", headers={"X-Mindboard-Token": "t"},
                     json={"question": "Which architecture?"})
    assert res.status_code == 200, res.text
    names = [b.split("\n")[0].removeprefix("event: ") for b in res.text.strip().split("\n\n")]
    assert names[0] == "plan" and names.count("step") == 4 and "context" in names and names[-1] == "done"
    assert seen_held == [True] * 4, "every call of the run happens with the model held"
    assert not model._asking.locked(), "and it is let go at the end"
