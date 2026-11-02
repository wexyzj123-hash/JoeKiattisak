import pytest

from safety.db import connect
from safety.risk import calc_score, level_for_score, list_levels, max_score, scale_size, score_bounds, validate_levels
from safety.schema import ensure_schema, seed_risk


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def lv(name, lo, hi, color="#2ecc71", sla=7, approval=0):
    return {"name_th": name, "min_score": lo, "max_score": hi, "color": color, "sla_days": sla, "requires_approval": approval}


def test_defaults_follow_the_specified_criteria(db):
    """เกณฑ์ตามเอกสาร: ความรุนแรง 12/6/2 + ความถี่ 5/4/3 + มาตรการ 8/4/1 รวม 6-25 และ 6 ระดับ A1-C"""
    assert (scale_size(db, "severity"), scale_size(db, "likelihood"), scale_size(db, "measure")) == (3, 3, 3)
    pts = lambda k: [r["points"] for r in db.execute("SELECT points FROM risk_scales WHERE kind=? ORDER BY level", (k,))]
    assert (pts("severity"), pts("likelihood"), pts("measure")) == ([12, 6, 2], [5, 4, 3], [8, 4, 1])
    assert score_bounds(db) == (6, 25) and max_score(db) == 25
    levels = list_levels(db)
    assert [(l["name_th"][:2].strip(), l["min_score"], l["max_score"]) for l in levels] == [
        ("C", 6, 9), ("B3", 10, 12), ("B2", 13, 15), ("B1", 16, 18), ("A2", 19, 21), ("A1", 22, 25)]
    assert {l["color"] for l in levels} == {"#ff0000", "#ffff00", "#00b050"}
    assert validate_levels([dict(l) for l in levels], 25, 6) == []


def test_seed_idempotent_and_keeps_edits(db):
    db.execute("UPDATE risk_levels SET name_th='เบา' WHERE name_th LIKE 'C %'")
    db.execute("UPDATE risk_scales SET label_th='x' WHERE kind='severity' AND level=1")
    db.commit()
    seed_risk(db)
    ensure_schema(db)
    assert "เบา" in [l["name_th"] for l in list_levels(db)] and len(list_levels(db)) == 6
    assert db.execute("SELECT label_th FROM risk_scales WHERE kind='severity' AND level=1").fetchone()[0] == "x"
    assert db.execute("SELECT COUNT(*) FROM risk_scales").fetchone()[0] == 9


def test_legacy_multiplicative_criteria_are_replaced_once(tmp_path):
    """เครื่องเดิมที่ใช้สูตรคูณ 5x5: อัปเกรดแล้วได้เกณฑ์ใหม่ครั้งเดียว และรายการเดิมยังเก็บคะแนน/ระดับตอนประเมินไว้"""
    con = connect(str(tmp_path / "old.db"))
    ensure_schema(con)
    con.execute("DROP TABLE risk_scales")
    con.execute("CREATE TABLE risk_scales (kind TEXT NOT NULL CHECK (kind IN ('likelihood','severity')), level INTEGER NOT NULL, "
                "label_th TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', PRIMARY KEY (kind, level))")
    con.execute("INSERT INTO risk_scales(kind, level, label_th) VALUES('likelihood', 1, 'เก่า')")
    con.execute("DELETE FROM app_settings WHERE key='risk_criteria_v2'")
    con.commit()
    ensure_schema(con)
    assert (scale_size(con, "measure"), score_bounds(con)) == (3, (6, 25))
    con.execute("UPDATE risk_scales SET points=99 WHERE kind='measure' AND level=1")
    con.commit()
    ensure_schema(con)
    assert con.execute("SELECT points FROM risk_scales WHERE kind='measure' AND level=1").fetchone()[0] == 99      # ไม่ทับซ้ำ


def test_calc_score_is_the_sum_of_three_factors(db):
    assert calc_score(db, 1, 1, 1) == 25 and calc_score(db, 3, 3, 3) == 6        # สูงสุด / ต่ำสุด
    assert calc_score(db, 2, 2, 2) == 6 + 4 + 4                                  # B + Moderate + Moderate = 14 (B2)
    assert level_for_score(db, calc_score(db, 2, 2, 2))["name_th"].startswith("B2")
    for bad in ((0, 3, 1), (3, 0, 1), (4, 1, 1), (1, 1, 4), (-1, 2, 1)):
        with pytest.raises(ValueError):
            calc_score(db, *bad)
    with pytest.raises(ValueError):
        calc_score(db, 1, 1)                        # ต้องเลือกมาตรการด้วย
    with pytest.raises(ValueError):
        calc_score(db, "3", 3, 1)
    with pytest.raises(ValueError):
        calc_score(db, True, 3, 1)


@pytest.mark.parametrize("score, name", [(6, "C"), (9, "C"), (10, "B3"), (12, "B3"), (13, "B2"), (15, "B2"), (16, "B1"),
                                         (18, "B1"), (19, "A2"), (21, "A2"), (22, "A1"), (25, "A1")])
def test_level_for_score_boundaries(db, score, name):
    assert level_for_score(db, score)["name_th"].split()[0] == name


@pytest.mark.parametrize("score", [0, 5, 26, -3])
def test_level_for_score_outside_none(db, score):
    assert level_for_score(db, score) is None


def test_bounds_follow_edited_points(db):
    db.execute("UPDATE risk_scales SET points=20 WHERE kind='severity' AND level=1")
    db.commit()
    assert score_bounds(db) == (6, 33)


GOOD = [lv("ต่ำ", 1, 4), lv("กลาง", 5, 9, "#f1c40f"), lv("สูง", 10, 25, "#ff4d4f")]


@pytest.mark.parametrize("levels, fragment", [
    ([lv("ต่ำ", 1, 4), lv("สูง", 6, 25)], "ช่องว่าง"),                       # ขาด 5
    ([lv("ต่ำ", 1, 5), lv("สูง", 5, 25)], "ซ้อน"),                           # ซ้อน 5
    ([lv("ต่ำ", 1, 4), lv("สูง", 5, 26)], "ช่วงคะแนน"),                      # เกินคะแนนสูงสุด
    ([lv("ต่ำ", 0, 4), lv("สูง", 5, 25)], "ช่วงคะแนน"),
    ([lv("ต่ำ", 5, 4), lv("สูง", 5, 25)], "ช่วงคะแนน"),                      # min > max
    ([lv("ต่ำ", 1, 24)], "ช่องว่าง"),                                        # ไม่ครอบคลุมถึง 25
    ([lv("ต่ำ", 1, 4, color="red"), lv("สูง", 5, 25)], "สี"),
    ([lv("ต่ำ", 1, 4, color="#12345"), lv("สูง", 5, 25)], "สี"),
    ([lv("ต่ำ", 1, 4, sla=-1), lv("สูง", 5, 25)], "SLA"),
    ([lv("ต่ำ", 1, 4, sla="abc"), lv("สูง", 5, 25)], "SLA"),
    ([lv("  ", 1, 4), lv("สูง", 5, 25)], "ชื่อ"),
    ([lv("ซ้ำ", 1, 4), lv("ซ้ำ", 5, 25)], "ซ้ำ"),
    ([], "ระดับ"),
])
def test_validate_levels_rejects(levels, fragment):
    errors = validate_levels(levels, 25)
    assert errors and any(fragment in e for e in errors), errors


def test_validate_levels_accepts_valid_and_order_independent():
    assert validate_levels(GOOD, 25) == []
    assert validate_levels(list(reversed(GOOD)), 25) == []
