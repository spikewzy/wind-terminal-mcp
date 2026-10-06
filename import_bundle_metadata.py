"""Import the six inspected, non-credential metadata files from Wind API.app."""
import json

from wind_bridge.bundle_metadata import INDEX, import_bundle

if __name__ == "__main__":
    result = import_bundle()
    print(json.dumps({"output": str(INDEX), "metadata_source_id": result["metadata_source_id"],
                      "counts": result["counts"], "decoding": result["decoding"]}, ensure_ascii=False))
