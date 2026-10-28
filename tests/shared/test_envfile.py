import os

from shared.envfile import load_env


def test_load_env_parses_and_does_not_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\n\nA_ONE=1\nA_TWO = two words \nA_QUOTED=\"q v\"\nBAD LINE\nA_KEEP=file\n", encoding="utf-8")
    monkeypatch.setenv("A_KEEP", "real-env")
    for k in ("A_ONE", "A_TWO", "A_QUOTED"):
        monkeypatch.delenv(k, raising=False)
    load_env(str(f))
    assert (os.environ["A_ONE"], os.environ["A_TWO"], os.environ["A_QUOTED"]) == ("1", "two words", "q v")
    assert os.environ["A_KEEP"] == "real-env"


def test_load_env_missing_file_is_noop(tmp_path):
    load_env(str(tmp_path / "nope.env"))
