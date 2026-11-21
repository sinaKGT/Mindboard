"""Understanding a request, then doing it in steps (DESIGN.md 66)."""
import json
import time

from mindboard import tasks
from mindboard.ask import Context, messages


class Model:
    """The model as the task layer sees it: a planning reply, a check, and a
    step's working, each said as a stream; stop as asked."""

    def __init__(self, plan, check=None, work="Worked out: three points.", slow=0.0):
        self.plan, self.check, self.work, self.slow = plan, check, work, slow
        self.prompts, self._stop = [], False

    def stream(self, msgs, **kw):
        system = msgs[0]["content"] if msgs[0]["role"] == "system" else ""
        self.prompts.append(msgs[-1]["content"])
        if system.startswith("You read a person's request"):
            reply = self.plan if isinstance(self.plan, str) else json.dumps(self.plan)
        elif system.startswith("You check whether"):
            reply = json.dumps(self.check or {"done": True})
        else:
            reply = self.work
        time.sleep(self.slow)
        yield {"type": "text", "text": reply}
        yield {"type": "done", "stopped": False}

    def cancelled(self):
        return self._stop


SUMMARY = {"task": "summarize", "about": "the pasted slide", "output": "short",
           "reading": "You want the slide summed up.",
           "steps": [{"do": "read", "what": "the whole slide", "uses": 0},
                     {"do": "work_out", "what": "its main points under its headings", "uses": 1},
                     {"do": "write", "what": "the summary"}]}


def tools(read=None, search=None):
    return tasks.Tools("Cards are in front of the model.", "the board", read=read, search=search)


def test_a_request_is_understood_as_a_task_with_only_steps_that_can_be_done():
    plan, trouble = tasks.understand(Model(SUMMARY), "summarize this for me", [], tools(read=lambda w: "x"))
    assert trouble == "" and plan["task"] == "summarize" and plan["output"] == "short"
    assert [s["do"] for s in plan["steps"]] == ["read", "work_out"], "no 'write' step; the answer is written after"
    assert plan["steps"][1]["uses"] == 1
    no_read, _ = tasks.understand(Model(SUMMARY), "summarize", [], tools())
    assert [s["do"] for s in no_read["steps"]] == ["work_out"], "no picture: nothing to read"
    assert tasks.understand(Model("sure, I'll summarize"), "q", [], tools())[1] == "could not be read"
    assert tasks.clean({"task": "sing"}, {"work_out": ""})["task"] == "answer"


def test_the_steps_run_in_a_chain_each_seeing_what_came_before():
    read, searched = [], []
    plan = {**SUMMARY, "steps": [{"do": "read", "what": "the slide"},
                                 {"do": "search", "what": "observability"},
                                 {"do": "work_out", "what": "main points", "uses": 1}]}
    model = Model(plan)
    ctx = Context(["A" * 26], "CARDS")

    def search(q):
        searched.append(q)
        return f"[{'B' * 26}] OpenTelemetry card", ["B" * 26]

    evs = list(tasks.run(model, ctx, "summarize this", [], tools(
        read=lambda w: read.append(w) or "Objective: track live performance.", search=search)))
    assert evs[0]["event"] == "task" and evs[0]["task"] == "summarize" and len(evs[0]["steps"]) == 3
    assert [(e["i"], e["status"]) for e in evs if e["event"] == "step"] == [
        (0, "running"), (0, "done"), (1, "running"), (1, "done"), (2, "running"), (2, "done")]
    assert read == ["the slide"] and searched == ["observability"]
    assert "Objective: track live performance." in model.prompts[-2], "the working step saw the reading"
    assert ctx.cards == ["A" * 26, "B" * 26], "a card a step found can be cited"
    note = ctx.task_note
    assert "WORK DONE TOWARD THIS" in note and "OpenTelemetry card" in note
    assert tasks.SHAPES["summarize"] in note and "The person asked for: short" in note
    assert note in messages(ctx, "summarize this", [])[-1]["content"], "said with the question"


def test_a_plain_question_has_no_steps_but_keeps_its_shape():
    ctx = Context([], "CARDS")
    evs = list(tasks.run(Model({"task": "answer", "steps": [], "reading": "You want a date."}), ctx,
                         "when was the last visit?", [], tools()))
    assert [e["event"] for e in evs] == ["task"]
    assert "WORK DONE" not in ctx.task_note and "You want a date." in ctx.task_note


def test_the_check_may_add_one_step_and_a_failing_step_is_said_not_guessed():
    plan = {"task": "compare", "reading": "You want A and B compared.",
            "steps": [{"do": "search", "what": "A"}, {"do": "search", "what": "B"}]}
    calls = []

    def search(q):
        calls.append(q)
        if q == "B":
            raise RuntimeError("index gone")
        return "about " + q, []

    model = Model(plan, check={"done": False, "step": {"do": "search", "what": "costs of A and B"}})
    ctx = Context([], "CARDS")
    evs = list(tasks.run(model, ctx, "compare A and B", [], tools(search=search)))
    assert calls == ["A", "B", "costs of A and B"], "one step added after the check, once"
    assert {"event": "task_step", "do": "search", "what": "costs of A and B"} in evs
    assert {"event": "step", "i": 1, "status": "failed", "note": "could not be done"} in evs
    assert "STEP 2" not in ctx.task_note, "a failed step hands on nothing to be read as a finding"


def test_a_slow_understanding_is_given_up_and_the_question_answered_as_it_stands(monkeypatch):
    monkeypatch.setattr(tasks, "UNDERSTAND_SECONDS", 0.0)
    ctx = Context([], "CARDS")
    assert list(tasks.run(Model(SUMMARY, slow=0.05), ctx, "summarize", [], tools())) == []
    assert ctx.task_note == ""


def test_a_stop_ends_the_steps():
    model = Model(SUMMARY)
    ctx = Context([], "CARDS")
    seen = []
    for ev in tasks.run(model, ctx, "summarize", [], tools(read=lambda w: "text")):
        seen.append(ev)
        if ev["event"] == "step" and ev["status"] == "running":
            model._stop = True
    assert seen[-1] == {"event": "step", "i": 0, "status": "stopped"} and ctx.task_note == ""


def test_a_lone_search_step_is_the_search_already_made_and_is_dropped():
    got, _ = tasks.understand(Model({"task": "extract", "steps": [{"do": "search", "what": "latest visit"}]}),
                              "when was the latest visit?", [], tools(search=lambda q: ("", [])))
    assert got["steps"] == []
    two = tasks.clean({"task": "compare", "steps": [{"do": "search", "what": "A"}, {"do": "search", "what": "B"}]},
                      {"search": "", "work_out": ""})
    assert len(two["steps"]) == 2


def test_a_quiz_is_its_own_task_asked_one_question_at_a_time():
    # DESIGN.md 72: read as "plan", a quiz came back as a plan for a quiz
    plan = tasks.clean({"task": "Quiz", "steps": []}, {"search": "x"})
    assert plan["task"] == "quiz"
    note = tasks.task_note(plan, [])
    assert "only the NEXT question" in note and "never its answer" in note


def test_a_card_actions_task_is_taken_as_it_is_without_reading_the_request():
    # DESIGN.md 75: read first, a fun fact came back as "LIST" and the page
    # said "Understanding your request" for a plain summary
    model = Model({"task": "extract", "steps": []})
    ctx = Context(["A" * 26], "CARDS")
    evs = list(tasks.run(model, ctx, "One fun fact from the card \"Analogy\".", [], tools(), known="funfact"))
    assert [e["event"] for e in evs] == ["task"] and evs[0]["task"] == "funfact"
    assert model.prompts == []                       # the request was not read by the model
    assert "Fun fact:" in ctx.task_note and "Never just restate" in ctx.task_note
    # anything else is read as before
    list(tasks.run(model, Context([], "CARDS"), "what is this?", [], tools(), known="extract"))
    assert len(model.prompts) == 1
