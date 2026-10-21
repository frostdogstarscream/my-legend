"""Load capabilities from the pinned native visual package, including verified assets."""
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sys

from .contracts import fingerprint
from .repository import ProductionError
from .stage_layout import layout_catalog
from .native_segments import VERSION


def catalog_from_paths(paths_file, profile):
    paths_file = Path(paths_file)
    paths = json.loads(paths_file.read_text(encoding="utf-8"))
    lock = json.loads((paths_file.parent / "dependency-lock.json").read_text(encoding="utf-8"))
    for relative, digest in lock["files"].items():
        path = paths_file.parent / relative
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ProductionError("DEPENDENCY_CHANGED")
    root = Path(paths["visual_root"]) / "app/visual_workflow"
    package = "_legend_visual_" + sha256(str(root.resolve()).encode()).hexdigest()[:12]
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(package, root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    native = importlib.import_module(package + ".asset_catalog")
    catalog = native.load_catalog()
    capabilities = native.target_capabilities("web_basic_v1")
    if profile == "standing_performance/1":
        capabilities["actions"] = ["enter", "exit", "move", "wait"]
    elif profile not in ("pixel_performance/1", "basic_storyboard/1"):
        raise ProductionError("VISUAL_PROFILE_UNSUPPORTED")
    return {"catalog": catalog, "catalog_sha256": fingerprint(catalog), "capabilities": capabilities,
            "execution_contract_sha256": fingerprint({'native_continuation':VERSION,
                'stage_layout':layout_catalog(), 'profile':profile, 'capabilities':capabilities}),
            "profile": profile, "native_package": package,
            "native_source_sha256": sha256((root / "asset_catalog.py").read_bytes()).hexdigest(),
            "stage_layout": layout_catalog(),
            "production_continuation": {"version": VERSION,
                "capacity_scope": "per_native_batch", "split_at": "source_event_boundary",
                "carried_state": ["position", "direction", "visible"], "single_event_over_capacity": "reject",
                "continuous_cast_limit": capabilities["max_scene_cast"]},
            "supports_action_fallback": False}
