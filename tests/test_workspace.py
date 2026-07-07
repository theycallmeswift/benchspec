"""Tests for workspace."""

from evalspec import workspace


def test_next_iteration_name_global_under_evals(tmp_path: object) -> None:
    """Verify next iteration name global under evals."""
    (tmp_path / "tmp" / "evals" / "iteration_02").mkdir(parents=True)
    (tmp_path / "tmp" / "evals" / "iteration_05").mkdir(parents=True)
    assert workspace.next_iteration_name(tmp_path) == "iteration_06"


def test_next_iteration_name_empty_starts_at_one(tmp_path: object) -> None:
    """Verify next iteration name empty starts at one."""
    assert workspace.next_iteration_name(tmp_path) == "iteration_01"


def test_next_iteration_name_ignores_non_iteration_dirs(tmp_path: object) -> None:
    """Verify next iteration name ignores non iteration dirs."""
    root = tmp_path / "tmp" / "evals"
    root.mkdir(parents=True)
    (root / "iteration_03").mkdir()
    (root / "scratch").mkdir()
    (root / "iteration_notanint").mkdir()
    (root / "iteration_07").write_text("")  # stray file, not a dir — must not be counted

    assert workspace.next_iteration_name(tmp_path) == "iteration_04"


def test_next_iteration_name_widens_past_pad_and_skips_non_decimal(
    tmp_path: object,
) -> None:
    """Verify next iteration name widens past pad and skips non decimal."""
    root = tmp_path / "tmp" / "evals"
    root.mkdir(parents=True)
    (root / "iteration_99").mkdir()
    (root / "iteration_²").mkdir()  # non-decimal tail — must be ignored, not crash

    assert workspace.next_iteration_name(tmp_path) == "iteration_100"


def test_set_and_arm_dir(tmp_path: object) -> None:
    """Verify set and arm dir."""
    workspace.set_current_iteration("iteration_07")
    assert workspace.current_iteration() == "iteration_07"

    d = workspace.arm_dir(tmp_path, "archive", "happy-path", "with_skill", sample=0)

    assert d == (
        tmp_path
        / "tmp"
        / "evals"
        / "iteration_07"
        / "skills"
        / "archive"
        / "eval-happy-path"
        / "with_skill"
        / "sample-0"
    )


def test_skill_dir_groups_skills_under_iteration(tmp_path: object) -> None:
    """Verify skill dir groups skills under iteration."""
    workspace.set_current_iteration("iteration_07")

    a = workspace.skill_dir(tmp_path, "archive")
    b = workspace.skill_dir(tmp_path, "ingest")

    assert a.parent == b.parent  # shared skills/ dir
    assert a.parent.name == "skills"
    assert a.parent.parent.name == "iteration_07"


def test_arm_dir_shards_per_sample(tmp_path: object) -> None:
    """Verify arm dir shards per sample."""
    workspace.set_current_iteration("iteration_01")

    d0 = workspace.arm_dir(tmp_path, "archive", "alpha", "with_skill", sample=0)
    d1 = workspace.arm_dir(tmp_path, "archive", "alpha", "with_skill", sample=1)

    assert d0 != d1
    assert d0.name == "sample-0"
    assert d1.name == "sample-1"
    assert d0.parent == d1.parent


def test_trigger_dir_shards_per_sample(tmp_path: object) -> None:
    """Verify trigger dir shards per sample."""
    workspace.set_current_iteration("iteration_01")

    d0 = workspace.trigger_dir(tmp_path, "archive", "archive-save-article", sample=0)
    d1 = workspace.trigger_dir(tmp_path, "archive", "archive-save-article", sample=1)

    assert d0 != d1
    assert d0.name == "sample-0"
    assert d1.name == "sample-1"
    assert d0.parent == d1.parent
    assert d0.parent.name == "trigger-archive-save-article"


def test_trigger_dir_uses_slug(tmp_path: object) -> None:
    """Verify trigger dir uses slug."""
    workspace.set_current_iteration("iteration_01")
    d = workspace.trigger_dir(tmp_path, "ingest", "ingest-article", sample=0)
    assert d.name == "sample-0"
    assert d.parent.name == "trigger-ingest-article"


def test_current_iteration_or_none_unset(monkeypatch: object) -> None:
    """Verify current iteration or none unset."""
    monkeypatch.delenv("EVALSPEC_ITERATION", raising=False)
    assert workspace.current_iteration_or_none() is None


def test_current_iteration_or_none_set(monkeypatch: object) -> None:
    """Verify current iteration or none set."""
    monkeypatch.setenv("EVALSPEC_ITERATION", "iteration_07")
    assert workspace.current_iteration_or_none() == "iteration_07"
