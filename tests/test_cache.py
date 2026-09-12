import json
import multiprocessing
from pathlib import Path

from juried.cache import Cache

LINE_LENGTH = 20_000


def append_many(root: Path, worker: int) -> None:
    cache = Cache(root)
    for index in range(20):
        cache.append_log("verdicts", {"worker": worker, "index": index, "pad": "x" * LINE_LENGTH})


def test_log_lines_stay_whole_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=append_many, args=(tmp_path, n)) for n in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    lines = (tmp_path / "verdicts.jsonl").read_text().splitlines()
    assert len(lines) == 80
    records = [json.loads(line) for line in lines]
    assert all(len(record["pad"]) == LINE_LENGTH for record in records)
    assert sorted((r["worker"], r["index"]) for r in records) == [
        (w, i) for w in range(4) for i in range(20)
    ]
