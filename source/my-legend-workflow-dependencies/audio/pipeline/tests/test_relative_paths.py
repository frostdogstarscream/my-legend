import json
import sys
from pathlib import Path

from lingban_pipeline.cli import main
from lingban_pipeline.io import file_hash, load, native


def test_extended_io_collapses_relative_parent_segments(tmp_path, monkeypatch):
    source = tmp_path / 'config.json'
    source.write_text('{"ready":true}', encoding='utf-8')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    relative = Path('..') / 'config.json'
    assert load(relative) == {'ready': True}
    assert file_hash(relative) == file_hash(source)
    assert '..' not in Path(native(relative)).parts


def test_cli_accepts_relative_input_and_config_from_another_directory(tmp_path, monkeypatch, capsys, make_project):
    script, config, _ = make_project(count=1, effects=False)
    (tmp_path / 'script.json').write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, 'argv', ['lingban', 'run', '--input', '../script.json', '--config', '../config.json', '--root', 'output', '--thread', 'relative-cli'])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'awaiting_review'
    assert result['technical_passed'] is True
    assert (work / 'output/drafts/r1/draft.wav').is_file()
