import json

from evalspec.trajectory import (
    extract_trajectory,
    render_process_facts,
    skills_dispatched,
)


def _assistant_tool_use(name, inp, tool_id="toolu_1"):
    return json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": inp}
        ]},
    })


def _user_tool_result(tool_use_id, content, is_error=False):
    return json.dumps({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": content, "is_error": is_error}
        ]},
    })


def test_extract_trajectory_pairs_calls_and_results_by_id():
    stdout = "\n".join([
        _assistant_tool_use("Write", {"file_path": "SKILL.md"}, tool_id="toolu_A"),
        _user_tool_result("toolu_A", "ok"),
    ])
    traj = extract_trajectory(stdout)
    assert traj == [
        {"kind": "tool_call", "id": "toolu_A", "name": "Write",
         "arguments": {"file_path": "SKILL.md"}},
        {"kind": "tool_result", "call_id": "toolu_A", "is_error": False, "content": "ok"},
    ]


def test_extract_trajectory_reads_list_content_blocks():
    # Claude tool_result content is often a list of {type:text,text} blocks.
    stdout = _user_tool_result("toolu_X", [{"type": "text", "text": "line1"}])
    traj = extract_trajectory(stdout)
    assert traj == [
        {"kind": "tool_result", "call_id": "toolu_X", "is_error": False, "content": "line1"},
    ]


def test_extract_trajectory_truncates_huge_result_content():
    big = "x" * 5000
    stdout = _user_tool_result("toolu_Y", big)
    traj = extract_trajectory(stdout)
    assert len(traj[0]["content"]) == 2000


def test_extract_trajectory_skips_blank_and_malformed_lines():
    stdout = "\n".join(["", "  ", "not json",
                        _assistant_tool_use("Read", {"file_path": "a"})])
    traj = extract_trajectory(stdout)
    assert [e["name"] for e in traj if e["kind"] == "tool_call"] == ["Read"]


def test_extract_trajectory_tolerates_null_name_and_input():
    # Partial/streamed events can carry null name or input — must not crash.
    stdout = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t", "name": None, "input": None}]},
    })
    traj = extract_trajectory(stdout)
    assert traj == [{"kind": "tool_call", "id": "t", "name": "", "arguments": {}}]


def test_extract_trajectory_tolerates_null_result_content():
    # A tool_result whose content is null must yield "" content, not crash.
    stdout = json.dumps({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": None, "is_error": False}
        ]},
    })
    traj = extract_trajectory(stdout)
    assert traj == [{"kind": "tool_result", "call_id": "t", "is_error": False, "content": ""}]


def test_skills_dispatched_names_each_skill_in_order_with_repeats():
    stdout = "\n".join([
        _assistant_tool_use("Skill", {"skill": "writing-prompts"}, tool_id="t1"),
        _assistant_tool_use("Write", {"file_path": "x"}, tool_id="t2"),
        _assistant_tool_use("Skill", {"skill": "writing-prompts"}, tool_id="t3"),
    ])
    assert skills_dispatched(extract_trajectory(stdout)) == ["writing-prompts", "writing-prompts"]


def test_skills_dispatched_empty_when_no_skill_calls():
    stdout = _assistant_tool_use("Bash", {"command": "ls"})
    assert skills_dispatched(extract_trajectory(stdout)) == []


def test_skills_dispatched_collects_namespaced_tool_fallback_when_skill_named():
    # A skill can fire as a tool_use whose name IS the skill (the second shape
    # detect_skill_fired matches). With skill_name given, that fallback is collected
    # so the skill_invoked activation assertion doesn't grade a false negative.
    stdout = _assistant_tool_use("knowledge-base:ingest", {"arg": "x"})

    assert skills_dispatched(extract_trajectory(stdout), "ingest") == ["knowledge-base:ingest"]


def test_skills_dispatched_ignores_fallback_without_skill_name():
    stdout = _assistant_tool_use("knowledge-base:ingest", {"arg": "x"})

    assert skills_dispatched(extract_trajectory(stdout)) == []


def test_render_process_facts_groups_by_turn_and_marks_skills():
    t1 = extract_trajectory("\n".join([
        _assistant_tool_use("Skill", {"skill": "writing-prompts"}, tool_id="t1"),
        _assistant_tool_use("Write", {"file_path": "SKILL.md"}, tool_id="t2"),
    ]))
    t2 = extract_trajectory(_assistant_tool_use("Bash", {"command": "make test"}))
    out = render_process_facts([t1, t2])
    assert "Turn 1: Skill(writing-prompts), Write" in out
    assert "Turn 2: Bash" in out


def test_render_process_facts_empty_when_no_tool_activity():
    assert render_process_facts([[], []]) == ""


def test_split_session_recovers_per_turn_streams():
    from evalspec.trajectory import split_session
    t1 = _assistant_tool_use("Skill", {"skill": "writing-prompts"}, tool_id="a")
    t2 = _assistant_tool_use("Bash", {"command": "ls"}, tool_id="b")
    session = (
        json.dumps({"turn": 1}) + "\n" + t1 + "\n"
        + json.dumps({"turn": 2}) + "\n" + t2 + "\n"
    )
    turns = split_session(session)
    assert [i for i, _ in turns] == [1, 2]
    assert extract_trajectory(turns[0][1])[0]["name"] == "Skill"
    assert extract_trajectory(turns[1][1])[0]["name"] == "Bash"


def test_split_session_empty_and_no_delimiter():
    from evalspec.trajectory import split_session
    assert split_session("") == []
    assert split_session('{"type":"result"}') == []  # no delimiter → nothing to split


def test_trajectory_from_session_tags_events_with_turn():
    from evalspec.trajectory import trajectory_from_session
    t1 = _assistant_tool_use("Skill", {"skill": "writing-prompts"}, tool_id="a")
    t2 = _assistant_tool_use("Bash", {"command": "ls"}, tool_id="b")
    session = (
        json.dumps({"turn": 1}) + "\n" + t1 + "\n"
        + json.dumps({"turn": 2}) + "\n" + t2 + "\n"
    )
    assert trajectory_from_session(session) == [
        {"turn": 1, "kind": "tool_call", "id": "a", "name": "Skill",
         "arguments": {"skill": "writing-prompts"}},
        {"turn": 2, "kind": "tool_call", "id": "b", "name": "Bash",
         "arguments": {"command": "ls"}},
    ]


def test_trajectory_from_session_regenerates_opencode_turns():
    # session.jsonl carries no agent marker, so trajectory_from_session must sniff
    # the OpenCode stream shape (tool uses nested under `part`) and route to the
    # OpenCode extractor — otherwise an OpenCode session regenerates to [].
    from evalspec.trajectory import trajectory_from_session
    oc = "\n".join([
        json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "skill",
                    "state": {"status": "completed", "input": {"name": "archive"}}}}),
        json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "ls"}}}}),
    ])
    session = json.dumps({"turn": 1}) + "\n" + oc + "\n"
    assert trajectory_from_session(session) == [
        {"turn": 1, "kind": "tool_call", "id": "", "name": "Skill",
         "arguments": {"skill": "archive"}},
        {"turn": 1, "kind": "tool_call", "id": "", "name": "bash",
         "arguments": {"command": "ls"}},
    ]


def test_iter_events_yields_only_dict_events_skipping_noise():
    from evalspec.trajectory import iter_events
    text = "\n".join(['', '  ', 'not json', '[1,2]', '{"type":"assistant"}'])
    assert list(iter_events(text)) == [{"type": "assistant"}]


def test_render_process_facts_honors_start_index():
    t = extract_trajectory(_assistant_tool_use("Bash", {"command": "ls"}))
    assert render_process_facts([t], start=3) == "Turn 3: Bash"
