import pytest

from safety.db import connect
from safety.risk import calc_score, level_for_score, list_levels, max_score, scale_size, validate_levels
from safety.schema import ensure_schema, seed_risk


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def lv(name, lo, hi, color="#2ecc71", sla=7, approval=0):
    return {"name_th": name, "min_score": lo, "max_score": hi, "color": color, "sla_days": sla, "requires_approval": approval}


def test_defaults_seeded_5x5_with_four_levels_covering_1_to_25(db):
    assert (scale_size(db, "likelihood"), scale_size(db, "severity"), max_score(db)) == (5, 5, 25)
    levels = list_levels(db)
    assert [(l["name_th"], l["min_score"], l["max_score"], l["color"], l["sla_days"], l["requires_approval"])
            for l in levels] == [("ต่ำ", 1, 4, "#2ecc71", 30, 0), ("ปานกลาง", 5, 9, "#f1c40f", 14, 0),
                                 ("สูง", 10, 16, "#ff9f1c", 7, 0), ("สูงมาก", 17, 25, "#ff4d4f", 3, 1)]
    labels = [r["label_th"] for r in db.execute("SELECT label_th FROM risk_scales WHERE kind='likelihood' ORDER BY level")]
    assert labels == ["แทบไม่เกิด", "เกิดได้ยาก", "เกิดได้บางครั้ง", "เกิดบ่อย", "เกิดบ่อยมาก"]
    sev = [r["label_th"] for r in db.execute("SELECT label_th FROM risk_scales WHERE kind='severity' ORDER BY level")]
    assert sev == ["เล็กน้อย", "ปานกลาง", "ร้ายแรง", "รุนแรง", "รุนแรงมาก"]
    assert validate_levels([dict(l) for l in levels], 25) == []


def test_seed_idempotent_and_keeps_edits(db):
    db.execute("UPDATE risk_levels SET name_th='เบา' WHERE name_th='ต่ำ'")
    db.execute("UPDATE risk_scales SET label_th='x' WHERE kind='severity' AND level=1")
    db.commit()
    seed_risk(db)
    ensure_schema(db)
    assert "เบา" in [l["name_th"] for l in list_levels(db)] and len(list_levels(db)) == 4
    assert db.execute("SELECT label_th FROM risk_scales WHERE kind='severity' AND level=1").fetchone()[0] == "x"
    assert db.execute("SELECT COUNT(*) FROM risk_scales").fetchone()[0] == 10


def test_calc_score_and_out_of_range(db):
    assert calc_score(db, 3, 4) == 12 and calc_score(db, 1, 1) == 1 and calc_score(db, 5, 5) == 25
    for l, s in ((0, 3), (3, 0), (6, 1), (1, 6), (-1, 2)):
        with pytest.raises(ValueError):
            calc_score(db, l, s)
    with pytest.raises(ValueError):
        calc_score(db, "3", 3)                      # ต้องเป็น int จริง
    with pytest.raises(ValueError):
        calc_score(db, True, 3)


@pytest.mark.parametrize("score, name", [(1, "ต่ำ"), (4, "ต่ำ"), (5, "ปานกลาง"), (9, "ปานกลาง"), (10, "สูง"),
                                         (16, "สูง"), (17, "สูงมาก"), (25, "สูงมาก")])
def test_level_for_score_boundaries(db, score, name):
    assert level_for_score(db, score)["name_th"] == name


@pytest.mark.parametrize("score", [0, 26, -3])
def test_level_for_score_outside_none(db, score):
    assert level_for_score(db, score) is None


def test_max_score_follows_scale_sizes(db):
    db.execute("INSERT INTO risk_scales(kind, level, label_th) VALUES('likelihood', 6, 'ใหม่')")
    db.commit()
    assert scale_size(db, "likelihood") == 6 and max_score(db) == 30


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
