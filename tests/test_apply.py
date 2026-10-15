"""core/apply.py: plan and write the port result as Zephyr's board files (tmp dirs, no env vars)."""
import pytest

from dt_explorer.core import apply as ap

OVL = "/ {\n\tmy_led0: my_led@1 {\n\t\tstatus = \"okay\";\n\t};\n};\n"
CONF = "CONFIG_GPIO=y\n"
COMMENTS = "/* nothing to do */\n# only a comment\n"


def test_stems_single_soc_short_form():
    assert ap.board_file_stems("sam_e54_xpro/atsame54p20a", True) == (
        "sam_e54_xpro", ["sam_e54_xpro_atsame54p20a", "sam_e54_xpro"])


def test_stems_multi_soc_long_form():
    assert ap.board_file_stems("alpha/soc/cpu0", False) == ("alpha_soc_cpu0", ["alpha_soc_cpu0", "alpha"])


def test_stems_plain_board_and_revision_ignored():
    assert ap.board_file_stems("same54_xpro") == ("same54_xpro", ["same54_xpro"])
    assert ap.board_file_stems("alpha/soc@1.0.0", False)[0] == "alpha_soc"


@pytest.mark.parametrize("bad", ["", "a b", "a" + chr(92) + "b", "a:b"])
def test_stems_reject_bad_names(bad):
    with pytest.raises(ap.ApplyError):
        ap.board_file_stems(bad)


def test_single_soc_of(tmp_path):
    one = tmp_path / "one.yml"
    one.write_text("board:\n  name: b\n  socs:\n    - name: s1\n")
    two = tmp_path / "two.yml"
    two.write_text("board:\n  name: b\n  socs:\n    - name: s1\n    - name: s2\n")
    assert ap.single_soc_of(one) is True and ap.single_soc_of(two) is False
    assert ap.single_soc_of(tmp_path / "missing.yml") is False and ap.single_soc_of(None) is False


def test_is_comment_only():
    assert ap.is_comment_only("") and ap.is_comment_only(COMMENTS)
    assert ap.is_comment_only("/* a\n multi\n line */\n// x\n")
    assert not ap.is_comment_only(CONF) and not ap.is_comment_only("/* c */ &uart { };")


def test_plan_new_files_and_apply(tmp_path):
    plan = ap.plan_apply(tmp_path, "sam_e54_xpro/atsame54p20a", OVL, CONF)
    assert [f.action for f in plan.files] == ["new", "new"]
    assert plan.file("overlay").path == tmp_path / "boards" / "sam_e54_xpro.overlay"
    assert plan.file("conf").path == tmp_path / "boards" / "sam_e54_xpro.conf"
    assert not (tmp_path / "boards").exists()                    # planning writes nothing
    res = ap.apply(plan)
    assert [p.name for p in res.written] == ["sam_e54_xpro.overlay", "sam_e54_xpro.conf"]
    assert (tmp_path / "boards" / "sam_e54_xpro.overlay").read_bytes() == OVL.encode()


def test_second_apply_refuses_and_shows_diff(tmp_path):
    ap.apply(ap.plan_apply(tmp_path, "b/s", OVL, CONF))
    plan = ap.plan_apply(tmp_path, "b/s", OVL + "// more\n", CONF)
    ov, cf = plan.file("overlay"), plan.file("conf")
    assert ov.exists and ov.diff and "+// more" in ov.diff and ov.action == "exists"
    assert cf.identical and cf.action == "identical"
    res = ap.apply(plan)
    assert res.refused == [ov.path] and not res.written and not res.overwritten
    assert (tmp_path / "boards" / "b.overlay").read_text() == OVL        # untouched


def test_overwrite_only_when_named(tmp_path):
    ap.apply(ap.plan_apply(tmp_path, "b", "&a { };\n", CONF))
    plan = ap.plan_apply(tmp_path, "b", OVL, CONF)
    res = ap.apply(plan, overwrite={"overlay"})
    assert res.overwritten == [tmp_path / "boards" / "b.overlay"]
    assert (tmp_path / "boards" / "b.overlay").read_text() == OVL


def test_comment_only_not_written(tmp_path):
    plan = ap.plan_apply(tmp_path, "b/s", OVL, COMMENTS)
    assert plan.file("conf").action == "skip (comments only)"
    res = ap.apply(plan)
    assert [p.name for p in res.written] == ["b.overlay"]
    assert not (tmp_path / "boards" / "b.conf").exists()


def test_other_accepted_name_counts_as_existing(tmp_path):
    (tmp_path / "boards").mkdir()
    long_file = tmp_path / "boards" / "b_s.overlay"
    long_file.write_text("&x { };\n")
    plan = ap.plan_apply(tmp_path, "b/s", OVL, CONF, single_soc=True)
    ov = plan.file("overlay")
    assert ov.path == long_file and ov.exists and "conflicting" in ov.note
    ap.apply(plan)
    assert not (tmp_path / "boards" / "b.overlay").exists()              # no conflicting second file
    assert long_file.read_text() == "&x { };\n"


def test_source_board_files_are_refused(tmp_path):
    with pytest.raises(ap.ApplyError):
        ap.plan_apply(tmp_path, "same54_xpro/same54p20a", OVL, CONF, source_board="same54_xpro/same54p20a")
    other = ap.plan_apply(tmp_path, "sam_e54_xpro/x", OVL, CONF, source_board="same54_xpro/same54p20a")
    assert other.stem == "sam_e54_xpro"


def test_other_files_untouched(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "boards").mkdir()
    (tmp_path / "src" / "main.c").write_text("int x;\n")
    (tmp_path / "boards" / "same54_xpro.overlay").write_text("&src { };\n")
    ap.apply(ap.plan_apply(tmp_path, "sam_e54_xpro/x", OVL, CONF, source_board="same54_xpro/y"))
    assert (tmp_path / "src" / "main.c").read_text() == "int x;\n"
    assert (tmp_path / "boards" / "same54_xpro.overlay").read_text() == "&src { };\n"


def test_missing_app_dir(tmp_path):
    with pytest.raises(ap.ApplyError):
        ap.plan_apply(tmp_path / "nope", "b", OVL, CONF)
