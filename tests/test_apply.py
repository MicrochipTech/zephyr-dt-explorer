"""core/apply.py: plan and write the port result as Zephyr's board files (tmp dirs, no env vars)."""
from datetime import date

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
    # the port flow writes a NEW file as a marked block (source "port") too (P8.19b item 3)
    assert (tmp_path / "boards" / "sam_e54_xpro.overlay").read_text() == (
        f"/* dt_explorer port begin {date.today().isoformat()} */\n" + OVL + "/* dt_explorer end */\n")


def test_port_new_file_then_changed_result_gives_exactly_one_block(tmp_path):
    f = tmp_path / "boards" / "b.overlay"
    ap.apply(ap.plan_apply(tmp_path, "b/s", OVL, CONF, today="2026-10-15"))
    second = OVL.replace("my_led0", "my_led9")
    plan = ap.plan_apply(tmp_path, "b/s", second, CONF, today="2026-10-16")
    assert plan.file("overlay").action == "exists"
    res = ap.apply(plan, append={"overlay", "conf"})
    assert f in res.appended
    text = f.read_text()
    assert text.count("dt_explorer port begin") == 1 and text.count("dt_explorer end") == 1
    assert "my_led9" in text and "my_led0" not in text


def test_second_apply_refuses_and_shows_diff(tmp_path):
    ap.apply(ap.plan_apply(tmp_path, "b/s", OVL, CONF))
    plan = ap.plan_apply(tmp_path, "b/s", OVL + "// more\n", CONF)
    ov, cf = plan.file("overlay"), plan.file("conf")
    assert ov.exists and ov.diff and "+// more" in ov.diff and ov.action == "exists"
    assert cf.action == "exists" and cf.append_diff == ""         # the marked block in the file is up to date
    res = ap.apply(plan)
    assert ov.path in res.refused and not res.written and not res.overwritten
    assert OVL in (tmp_path / "boards" / "b.overlay").read_text() and "// more" not in (tmp_path / "boards" / "b.overlay").read_text()


def test_overwrite_only_when_named(tmp_path):
    ap.apply(ap.plan_apply(tmp_path, "b", "&a { };\n", CONF))
    plan = ap.plan_apply(tmp_path, "b", OVL, CONF)
    res = ap.apply(plan, overwrite={"overlay"})
    assert res.overwritten == [tmp_path / "boards" / "b.overlay"]
    text = (tmp_path / "boards" / "b.overlay").read_text()
    assert OVL in text and "&a { }" not in text and text.count("dt_explorer end") == 1


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


# ---- draft_plan (Draft tab "Apply to app", P8.13) ------------------------------------------------
def _own_board(monkeypatch, target="same54_xpro/same54p20a", single=True):
    monkeypatch.setattr(ap, "target_of", lambda build: (target, single))


def test_draft_plan_new_files(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    plan = ap.draft_plan(tmp_path / "build", OVL, CONF, app_dir=tmp_path)
    assert plan.stem == "same54_xpro"
    assert [(f.kind, f.action) for f in plan.files] == [("overlay", "new"), ("conf", "new")]
    res = ap.apply(plan)
    # a NEW file is written as a marked block as well (P8.19b item 3)
    today = date.today().isoformat()
    assert (tmp_path / "boards" / "same54_xpro.overlay").read_text() == (
        f"/* dt_explorer draft begin {today} */\n" + OVL + "/* dt_explorer end */\n")
    assert (tmp_path / "boards" / "same54_xpro.conf").read_text() == (
        f"# dt_explorer draft begin {today}\n" + CONF + "# dt_explorer end\n")
    assert len(res.written) == 2


def test_new_file_then_changed_draft_gives_exactly_one_block(tmp_path, monkeypatch):
    """P8.19b item 3: first Apply creates the file, a second Apply of a changed draft replaces the block."""
    _own_board(monkeypatch)
    f = tmp_path / "boards" / "same54_xpro.overlay"
    c = tmp_path / "boards" / "same54_xpro.conf"
    ap.apply(ap.draft_plan(tmp_path / "b", OVL, CONF, app_dir=tmp_path, today="2026-10-15"))
    second = OVL.replace("my_led0", "my_led9")
    plan = ap.draft_plan(tmp_path / "b", second, CONF + "CONFIG_I2C=y\n", app_dir=tmp_path, today="2026-10-16")
    assert plan.file("overlay").action == "exists"
    res = ap.apply(plan, append={"overlay", "conf"})
    assert sorted(res.appended) == sorted([f, c]) and not res.written
    text = f.read_text()
    assert text.count("dt_explorer draft begin") == 1 and text.count("dt_explorer end") == 1
    assert "my_led9" in text and "my_led0" not in text
    assert c.read_text().count("CONFIG_GPIO=y") == 1 and "CONFIG_I2C=y" in c.read_text()
    again = ap.draft_plan(tmp_path / "b", second, CONF + "CONFIG_I2C=y\n", app_dir=tmp_path, today="2026-10-16")
    assert again.file("overlay").append_diff == "" and again.file("conf").append_diff == ""      # nothing to do


def test_draft_plan_existing_own_overlay_is_refused_with_draft_diff(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    (tmp_path / "boards").mkdir()
    own = tmp_path / "boards" / "same54_xpro.overlay"
    OWN = "/ { own; };\n"
    own.write_text(OWN)
    plan = ap.draft_plan(tmp_path / "build", OVL, "", app_dir=tmp_path)
    fp = plan.file("overlay")
    assert fp.action == "exists" and "(draft)" in fp.diff and "(port result)" not in fp.diff
    assert plan.file("conf").comment_only
    res = ap.apply(plan)
    assert res.refused == [own] and own.read_text() == OWN
    res = ap.apply(plan, overwrite={"overlay"})
    assert res.overwritten == [own] and OVL in own.read_text() and own.read_text().count("dt_explorer end") == 1


def test_draft_plan_multi_soc_uses_long_name(tmp_path, monkeypatch):
    _own_board(monkeypatch, "alpha/soc/cpu0", False)
    assert ap.draft_plan(tmp_path / "b", OVL, CONF, app_dir=tmp_path).stem == "alpha_soc_cpu0"


# ---- append mode (P8.13b): marked block in the app's own file ------------------------------------
OWN = "/ {\n\tmy_led: my_led@1 { status = \"okay\"; };\n};\n"
DRAFT = "&sercom1 {\n\tstatus = \"okay\";\n};\n"
DRAFT2 = "&sercom1 {\n\tstatus = \"okay\";\n\tclock-frequency = <400000>;\n};\n"
BEGIN = "/* dt_explorer draft begin 2026-10-15 */\n"
END = "/* dt_explorer end */\n"


def _app_with(tmp_path, overlay=OWN, conf=None):
    (tmp_path / "boards").mkdir(exist_ok=True)
    (tmp_path / "boards" / "same54_xpro.overlay").write_text(overlay)
    if conf is not None:
        (tmp_path / "boards" / "same54_xpro.conf").write_text(conf)
    return tmp_path


def test_append_keeps_existing_and_adds_marked_block(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    _app_with(tmp_path)
    plan = ap.draft_plan(tmp_path / "b", DRAFT, "", app_dir=tmp_path, today="2026-10-15")
    fp = plan.file("overlay")
    assert fp.merged == OWN + "\n" + BEGIN + DRAFT + END
    assert "+/* dt_explorer draft begin 2026-10-15 */" in fp.append_diff
    assert "-" + OWN.splitlines()[0] not in fp.append_diff
    res = ap.apply(plan, append={"overlay"})
    assert res.appended == [tmp_path / "boards" / "same54_xpro.overlay"] and not res.overwritten
    assert (tmp_path / "boards" / "same54_xpro.overlay").read_text() == OWN + "\n" + BEGIN + DRAFT + END


def test_append_not_chosen_still_refuses(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    _app_with(tmp_path)
    res = ap.apply(ap.draft_plan(tmp_path / "b", DRAFT, "", app_dir=tmp_path))
    assert len(res.refused) == 1 and not res.appended


def test_reapply_replaces_only_the_block(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    f = _app_with(tmp_path) / "boards" / "same54_xpro.overlay"
    ap.apply(ap.draft_plan(tmp_path / "b", DRAFT, "", app_dir=tmp_path, today="2026-10-15"), append={"overlay"})
    first = f.read_text()
    again = ap.draft_plan(tmp_path / "b", DRAFT, "", app_dir=tmp_path, today="2026-10-15")
    assert again.file("overlay").append_diff == ""                       # same draft, same day: nothing to do
    assert f in ap.apply(again, append={"overlay"}).skipped and f.read_text() == first
    ap.apply(ap.draft_plan(tmp_path / "b", DRAFT2, "", app_dir=tmp_path, today="2026-10-16"), append={"overlay"})
    second = f.read_text()
    assert second == OWN + "\n" + BEGIN.replace("15", "16") + DRAFT2 + END      # one block, the app's part intact
    assert second.count("dt_explorer end") == 1 and second.startswith(OWN)


def test_text_after_the_block_survives_replacement():
    tail = "\n&uart0 { status = \"okay\"; };\n"
    merged, _ = ap.merge_block("overlay", OWN + "\n" + BEGIN + DRAFT + END + tail, DRAFT2, today="2026-10-15")
    assert merged == OWN + "\n" + BEGIN + DRAFT2 + END + tail


def test_append_without_trailing_newline_and_empty_file():
    assert ap.merge_block("overlay", "&a { };", DRAFT, today="D")[0] == \
        "&a { };\n\n/* dt_explorer draft begin D */\n" + DRAFT + END
    assert ap.merge_block("overlay", "", DRAFT, today="D")[0] == "/* dt_explorer draft begin D */\n" + DRAFT + END


def test_conf_append_once_skips_duplicates(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    _app_with(tmp_path, conf="CONFIG_GPIO=y\nCONFIG_I2C=y\n")
    conf = "# Kconfig lines for x\nCONFIG_I2C=y\nCONFIG_I2C_SAM0=y\n"
    plan = ap.draft_plan(tmp_path / "b", DRAFT, conf, app_dir=tmp_path, today="2026-10-15")
    assert "left out" in plan.file("conf").append_note
    ap.apply(plan, append={"overlay", "conf"})
    text = (tmp_path / "boards" / "same54_xpro.conf").read_text()
    assert text == ("CONFIG_GPIO=y\nCONFIG_I2C=y\n\n# dt_explorer draft begin 2026-10-15\n"
                    "# Kconfig lines for x\nCONFIG_I2C_SAM0=y\n# dt_explorer end\n")
    assert text.count("CONFIG_I2C=y") == 1
    again = ap.draft_plan(tmp_path / "b", DRAFT, conf, app_dir=tmp_path, today="2026-10-15")
    assert again.file("conf").merged == text and not again.file("conf").append_diff


def test_conf_all_duplicates_adds_no_block(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    _app_with(tmp_path, conf="CONFIG_I2C=y\n")
    plan = ap.draft_plan(tmp_path / "b", DRAFT, "# c\nCONFIG_I2C=y\n", app_dir=tmp_path)
    assert plan.file("conf").merged == "CONFIG_I2C=y\n" and plan.file("conf").append_diff == ""


def test_replace_wins_over_append(tmp_path, monkeypatch):
    _own_board(monkeypatch)
    f = _app_with(tmp_path) / "boards" / "same54_xpro.overlay"
    res = ap.apply(ap.draft_plan(tmp_path / "b", DRAFT, "", app_dir=tmp_path),
                   overwrite={"overlay"}, append={"overlay"})
    assert res.overwritten == [f] and DRAFT in f.read_text() and OWN not in f.read_text()


def test_port_plan_uses_port_marker(tmp_path):
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "b.overlay").write_text(OWN)
    plan = ap.plan_apply(tmp_path, "b", DRAFT, "", today="D")
    assert "/* dt_explorer port begin D */" in plan.file("overlay").merged
