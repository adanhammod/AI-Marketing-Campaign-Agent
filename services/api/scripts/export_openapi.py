import json
from pathlib import Path
from typing import Any

from campaign_api.main import create_app

OUTPUT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "generated" / "openapi.json"


def build_schema() -> dict[str, Any]:
    return create_app().openapi()


def main() -> None:
    schema = build_schema()
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
