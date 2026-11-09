#!/usr/bin/env python3
"""Convert one-partition CsvComposite SNB CSV headers without touching input."""
import argparse
from pathlib import Path
import shutil

HEADERS = Path(__file__).resolve().parents[1] / "doc/neo4j-vs-petgraphdb-benchmark-v2/scripts/headers.txt"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="datagen social_network directory")
    ap.add_argument("output", type=Path, help="new converted directory; must not exist")
    args = ap.parse_args()
    entries = [line.split(" ", 1) for line in HEADERS.read_text().splitlines() if line.strip()]
    for name, _ in entries:
        if not (args.source / (name + "_0_0.csv")).is_file():
            ap.error(f"Missing CSV: {name}_0_0.csv (requires one-partition CsvComposite)")
    args.output.mkdir(parents=True, exist_ok=False)
    for name, header in entries:
        relative = name + "_0_0.csv"
        dst = args.output / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        with (args.source / relative).open() as src, dst.open("w") as out:
            next(src)
            out.write(header + "\n")
            if name == "static/place":
                for line in src:
                    fields = line.rstrip("\n").split("|")
                    fields[-1] = {"city": "City", "country": "Country", "continent": "Continent"}.get(fields[-1], fields[-1])
                    out.write("|".join(fields) + "\n")
            elif name == "static/organisation":
                for line in src:
                    out.write(line.replace("|company|", "|Company|").replace("|university|", "|University|"))
            else:
                shutil.copyfileobj(src, out)
    print(f"Converted {len(entries)} CSVs to {args.output}")


if __name__ == "__main__":
    main()
