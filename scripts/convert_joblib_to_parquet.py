#!/usr/bin/env python3
import argparse
from pathlib import Path

import joblib
import pandas as pd
import numpy as np


def to_rows(property_dicts):
    rows = []
    for feature_name, feature_data in property_dicts.items():
        if not isinstance(feature_data, dict):
            continue
        cands = feature_data.get("cands", {})
        if not isinstance(cands, dict):
            continue
        for building_id, value in cands.items():
            if isinstance(value, (np.integer, np.floating)):
                value = float(value)
            elif isinstance(value, np.ndarray):
                value = value.tolist()
            rows.append(
                {
                    "building_id": str(building_id),
                    "feature_name": str(feature_name),
                    "value": value,
                }
            )
    return rows


def convert(input_path: Path, output_path: Path) -> None:
    data = joblib.load(input_path)
    if not isinstance(data, dict):
        raise ValueError("Expected dict in joblib file.")

    rows = to_rows(data)
    if not rows:
        raise ValueError("No rows extracted from joblib data.")

    df = pd.DataFrame(rows)
    df.to_parquet(output_path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert joblib features to Parquet.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to joblib file",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to output Parquet file",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Joblib file not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    convert(input_path, output_path)
    print(f"Saved Parquet: {output_path}")


if __name__ == "__main__":
    main()
