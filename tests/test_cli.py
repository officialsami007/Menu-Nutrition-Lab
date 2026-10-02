import sys

import analyze


def run(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["analyze.py", *args])
    code = analyze.main()
    return code, capsys.readouterr()


def test_default_report_prints_statistics_and_comparison(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys)
    assert code == 0
    assert "177 rows read, 74 kept" in out.out
    assert "Fat-to-protein ratio" in out.out and "Drinks vs food" in out.out


def test_filters_from_the_brief(monkeypatch, capsys):
    _, out = run(monkeypatch, capsys, "--show", "drinks", "--caffeine", "yes")
    assert "62 of 74" in out.out
    _, out = run(monkeypatch, capsys, "--show", "food", "--under-calories", "500")
    assert "99 of 113" in out.out


def test_bad_file_exits_with_message(monkeypatch, capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    code, out = run(monkeypatch, capsys, "--drinks", str(bad))
    assert code == 1 and "Could not load data" in out.err

