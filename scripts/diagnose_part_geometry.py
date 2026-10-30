"""Locate an exact rejected LDraw record without modifying the source library."""
import argparse
import json

from car2lego3d.part_library import PartLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--part", required=True)
    parser.add_argument("--package", action="append", default=[])
    args = parser.parse_args()
    library = PartLibrary(args.library, args.package)
    try:
        triangles, _ = library.triangles(args.part)
    except Exception as error:
        frames, trace = [], error.__traceback__
        while trace:
            context = trace.tb_frame.f_locals
            if "line" in context:
                frames.append(dict(file=str(context.get("path", "")), line=context["line"]))
            trace = trace.tb_next
        print(json.dumps(dict(part=args.part, error=f"{type(error).__name__}: {error}", frames=frames)))
        raise SystemExit(1)
    print(json.dumps(dict(part=args.part, triangles=len(triangles))))


if __name__ == "__main__":
    main()
