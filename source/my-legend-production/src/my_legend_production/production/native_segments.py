"""Versioned state continuation over pinned native rules, without editing the dependency."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from .contracts import fingerprint
from .repository import ProductionError
from .process_tree import run_process_tree, media_environment
from .stage_layout import HOME_SLOTS, LAYOUT_VERSION, EXIT_DESTINATION


SOURCE_HASHES = {
    "compiler.dart": "8bbb7e07ede164c3fab8793823ee3380abdbe285d36d8e68faec8c06cffa331f",
    "evaluator.dart": "eae1de88ed2718bf76fee4937f42a3cf9599b7c7f32c9ff001546b78640742c0",
}
VERSION = "native_state_continuation/1.2"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ProductionError("VISUAL_NATIVE_EXTENSION_SOURCE_CHANGED")
    return text.replace(old, new)


@contextmanager
def segment_compiler(paths, *, check=None):
    source = Path(paths["visual_root"]) / "pixel-story-mobile/packages/story_player/lib/src"
    files = {}
    for name, digest in SOURCE_HASHES.items():
        raw = (source / name).read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ProductionError("VISUAL_NATIVE_EXTENSION_SOURCE_CHANGED")
        files[name] = raw.decode("utf-8")
    files["compiler.dart"] = replace_once(files["compiler.dart"],
        "StoryManifest compile(StoryDocument input) {",
        "StoryManifest compile(StoryDocument input, {Map<String, ActorPose>? initialPoses}) {")
    files["compiler.dart"] = replace_once(files["compiler.dart"],
        "final current = Map<String, ActorPose>.of(initial);",
        "if (initialPoses != null) { initial.addAll(initialPoses); }\n"
        "      final current = Map<String, ActorPose>.of(initial);")
    files["compiler.dart"] = replace_once(files["compiler.dart"],
        "position: anchors['slot_${i + 1}']!,",
        "position: anchors[productionHomeSlot(scene.cast.length, i)]!,")
    files["compiler.dart"] = replace_once(files["compiler.dart"],
        "anchors['slot_${scene.cast.indexOf(role!) + 1}']!",
        "anchors[productionHomeSlot(scene.cast.length, scene.cast.indexOf(role!))]!")
    # The visible exit anchor remains a valid move target. Leaving from there
    # must continue off-stage rather than reusing the same point and vanishing.
    files["compiler.dart"] = replace_once(files["compiler.dart"],
        ": anchors[block.action == StoryAction.exit\n                    ? 'exit'\n                    : block.targetAnchor]!;",
        ": block.action == StoryAction.exit\n"
        f"                  ? const Point2({EXIT_DESTINATION[0]}, {EXIT_DESTINATION[1]})\n"
        "                  : anchors[block.targetAnchor]!;")
    layout_rows = ','.join(str(n)+':'+json.dumps(slots) for n,slots in HOME_SLOTS.items())
    files["compiler.dart"] += '\nString productionHomeSlot(int count, int index) => const <int,List<String>>{' + layout_rows + '}[count]![index];\n'
    for name in files:
        for dependency in ("model.dart", "templates.dart"):
            files[name] = files[name].replace(f"import '{dependency}';", f"import '{(source / dependency).resolve().as_uri()}';")
    files["bridge.dart"] = Path(__file__).with_name("native_segment_bridge.dart").read_text(encoding="utf-8")
    files["bridge.dart"] = files["bridge.dart"].replace("__MODEL__", (source / "model.dart").resolve().as_uri())
    files["bridge.dart"] = files["bridge.dart"].replace("__TEMPLATES__", (source / "templates.dart").resolve().as_uri())
    files["bridge.dart"] = files["bridge.dart"].replace("__EXIT_X__",str(EXIT_DESTINATION[0])).replace("__EXIT_Y__",str(EXIT_DESTINATION[1]))
    evidence = {"version": VERSION, "stage_layout": LAYOUT_VERSION, "native_sources": SOURCE_HASHES,
                "compiled_sources": {name: sha256(text.encode()).hexdigest() for name, text in files.items()}}
    with TemporaryDirectory(prefix="legend-native-segments-") as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).write_bytes(text.encode("utf-8"))
        def compile(document, initial_state=None):
            try:
                with TemporaryFile(mode="w+b") as output:
                    code = run_process_tree([paths["dart"], "--packages=" + paths["dart_packages"], str(root / "bridge.dart")],
                        input_text=json.dumps({"document": document, "initial_state": initial_state}, ensure_ascii=False),
                        log=output, timeout=30, check=check, env=media_environment())
                    if code:
                        raise ProductionError("VISUAL_NATIVE_COMPILE_FAILED")
                    output.seek(0)
                    data = json.loads(output.read().decode("utf-8"))
            except (OSError, ValueError):
                raise ProductionError("VISUAL_NATIVE_COMPILER_UNAVAILABLE") from None
            if not data.get("ok"):
                code = data.get("error", {}).get("code")
                raise ProductionError("VISUAL_NATIVE_CAPACITY_EXCEEDED" if code == "CAPACITY_EXCEEDED" else "VISUAL_NATIVE_COMPILE_FAILED")
            data["continuation_engine"] = evidence
            data["plan_hash"] = fingerprint({key: value for key, value in data.items() if key != "frames"})
            return data
        yield compile
