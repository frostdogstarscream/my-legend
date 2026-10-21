from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lingban_pipeline.contracts import StandardScript, PerformanceDocument, ProductionTask, AssetRecord, Timeline, ReviewRecord
from lingban_pipeline.io import save

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "contracts"
    for cls in (StandardScript, PerformanceDocument, ProductionTask, AssetRecord, Timeline, ReviewRecord):
        save(root / (cls.__name__ + ".schema.json"), cls.model_json_schema())
