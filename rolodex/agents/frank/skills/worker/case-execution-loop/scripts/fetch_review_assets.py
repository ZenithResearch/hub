#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.request
from pathlib import Path


def _download(url: str) -> tuple[bytes, str | None]:
    with urllib.request.urlopen(url) as response:
        return response.read(), response.headers.get_content_type()


def _suffix_for(asset_type: str, content_type: str | None) -> str:
    if asset_type == "events":
        return ".json"
    if content_type:
        guessed = mimetypes.guess_extension(content_type, strict=False)
        if guessed:
            return guessed
    if asset_type == "audio":
        return ".webm"
    return ".bin"


def _write_asset(output_dir: Path, asset_id: str, asset_type: str, gateway_url: str) -> str:
    body, content_type = _download(f"{gateway_url.rstrip('/')}/v1/reviews/assets/{asset_id}")
    suffix = _suffix_for(asset_type, content_type)
    path = output_dir / f"{asset_type}_{asset_id}{suffix}"
    path.write_bytes(body)
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch review assets by asset_id from gateway-http.")
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--events-asset-id", required=True)
    parser.add_argument("--audio-asset-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    events_path = _write_asset(output_dir, args.events_asset_id, "events", args.gateway_url)
    audio_path = _write_asset(output_dir, args.audio_asset_id, "audio", args.gateway_url)

    payload = {
        "review_id": args.review_id,
        "materialized_dir": str(output_dir),
        "events_asset_id": args.events_asset_id,
        "audio_asset_id": args.audio_asset_id,
        "events_asset_path": events_path,
        "audio_asset_path": audio_path,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
