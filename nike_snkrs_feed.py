#!/usr/bin/env python3
"""Fetch Nike SNKRS product feed data and save new product assets locally."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ENDPOINT = "https://api.nike.com/product_feed/rollup_threads/v2"
DEFAULT_SNKRS_CHANNEL_ID = "008be467-6c78-4079-94f0-70e2d6cc4003"
DEFAULT_DATABASE_PATH = BASE_DIR / "nike_snkrs_assets.sqlite3"
DEFAULT_IMAGE_DIR = BASE_DIR / "static" / "images"
TEMPLATES_DIR = BASE_DIR / "templates"
IMAGE_EXTENSIONS = (".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp")
COMMON_REQUEST_HEADERS = {
    "Origin": "https://www.nike.com",
    "Referer": "https://www.nike.com/launch",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
}
FEED_REQUEST_HEADERS = {
    **COMMON_REQUEST_HEADERS,
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
}
IMAGE_REQUEST_HEADERS = {
    **COMMON_REQUEST_HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


DATABASE_PATH = Path(os.environ.get("SNKRS_DATABASE", DEFAULT_DATABASE_PATH))
IMAGE_DIR = Path(os.environ.get("SNKRS_IMAGE_DIR", DEFAULT_IMAGE_DIR))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Nike SNKRS Asset Gallery")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the Nike SNKRS product feed and print each thread's product "
            "title, style-color code, and image URLs from publishedContent.nodes. "
            "New style codes are saved to SQLite with their first image downloaded "
            "locally."
        )
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Nike product feed endpoint. Defaults to {DEFAULT_ENDPOINT}",
    )
    parser.add_argument(
        "--marketplace",
        default="US",
        help="Nike marketplace filter value, for example US or GB. Defaults to US.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Nike language filter value, for example en or en-GB. Defaults to en.",
    )
    parser.add_argument(
        "--channel-id",
        default=DEFAULT_SNKRS_CHANNEL_ID,
        help=(
            "SNKRS consumer channel ID. Defaults to the SNKRS app channel ID "
            f"({DEFAULT_SNKRS_CHANNEL_ID})."
        ),
    )
    parser.add_argument(
        "--count",
        type=int,
        default=24,
        help="Number of product threads to request. Defaults to 24.",
    )
    parser.add_argument(
        "--anchor",
        type=int,
        default=0,
        help="Pagination anchor to request. Defaults to 0.",
    )
    parser.add_argument(
        "--filter",
        action="append",
        dest="extra_filters",
        default=[],
        help=(
            "Additional Nike feed filter, such as 'employeePrice(true)'. "
            "May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Request timeout in seconds. Defaults to 20.",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help="SQLite database path. Defaults to nike_snkrs_assets.sqlite3.",
    )
    parser.add_argument(
        "--image-dir",
        default=str(DEFAULT_IMAGE_DIR),
        help="Directory for downloaded product images. Defaults to static/images.",
    )
    return parser.parse_args()


def build_request_url(args: argparse.Namespace) -> str:
    query_params: list[tuple[str, str]] = [
        ("filter", f"marketplace({args.marketplace})"),
        ("filter", f"language({args.language})"),
    ]

    if "rollup_threads" in urllib.parse.urlparse(args.endpoint).path:
        query_params.append(("consumerChannelId", args.channel_id))
    else:
        query_params.append(("filter", f"channelId({args.channel_id})"))

    query_params.extend(("filter", value) for value in args.extra_filters)
    query_params.extend(
        [
            ("count", str(args.count)),
            ("anchor", str(args.anchor)),
        ]
    )

    parsed = urllib.parse.urlparse(args.endpoint)
    existing_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(existing_params + query_params)
    return urllib.parse.urlunparse(parsed._replace(query=query))


def read_response_body(response: Any) -> bytes:
    body = response.read()
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        return gzip.decompress(body)
    return body


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=FEED_REQUEST_HEADERS,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(read_response_body(response).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = read_response_body(exc).decode("utf-8", errors="replace")
        raise RuntimeError(f"Nike API returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to request Nike API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Nike API response was not valid JSON: {exc}") from exc


def get_product_threads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    objects = payload.get("objects")
    if isinstance(objects, list):
        return [item for item in objects if isinstance(item, dict)]

    nested_objects = (
        payload.get("data", {}).get("products", {}).get("objects")
        if isinstance(payload.get("data"), dict)
        else None
    )
    if isinstance(nested_objects, list):
        return [item for item in nested_objects if isinstance(item, dict)]

    return []


def unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue

        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


def get_nested(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_string(values: Iterable[Any], default: str = "N/A") -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def extract_title(thread: dict[str, Any]) -> str:
    product_info = thread.get("productInfo")
    infos = product_info if isinstance(product_info, list) else []

    candidates: list[Any] = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        candidates.extend(
            [
                get_nested(info, ("productContent", "fullTitle")),
                get_nested(info, ("productContent", "title")),
            ]
        )

    candidates.extend(
        [
            get_nested(thread, ("publishedContent", "properties", "title")),
            get_nested(thread, ("publishedContent", "properties", "subtitle")),
        ]
    )
    candidates.extend(
        get_nested(info, ("merchProduct", "labelName"))
        for info in infos
        if isinstance(info, dict)
    )
    return first_string(candidates)


def extract_style_colors(thread: dict[str, Any]) -> list[str]:
    product_info = thread.get("productInfo")
    infos = product_info if isinstance(product_info, list) else []

    style_colors: list[str] = []
    for info in infos:
        if not isinstance(info, dict):
            continue

        merch_product = info.get("merchProduct")
        if not isinstance(merch_product, dict):
            continue

        style_color = merch_product.get("styleColor")
        if isinstance(style_color, str) and style_color.strip():
            style_colors.append(style_color)
            continue

        style_code = merch_product.get("styleCode")
        color_code = merch_product.get("colorCode")
        if isinstance(style_code, str) and isinstance(color_code, str):
            style_colors.append(f"{style_code}-{color_code}")

    return unique_strings(style_colors)


def is_image_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    path = parsed.path.lower()
    host = parsed.netloc.lower()
    return (
        path.endswith(IMAGE_EXTENSIONS)
        or ("static.nike.com" in host and "/a/images/" in path)
    )


def iter_image_urls(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_image_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_image_urls(child)
    elif isinstance(value, str) and is_image_url(value):
        yield value


def extract_node_image_urls(thread: dict[str, Any]) -> list[str]:
    nodes = get_nested(thread, ("publishedContent", "nodes"))
    if nodes is None:
        return []
    return unique_strings(iter_image_urls(nodes))


def create_asset_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            style_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()


def asset_exists(connection: sqlite3.Connection, style_code: str) -> bool:
    cursor = connection.execute(
        "SELECT 1 FROM assets WHERE style_code = ? LIMIT 1",
        (style_code,),
    )
    return cursor.fetchone() is not None


def insert_asset(
    connection: sqlite3.Connection,
    style_code: str,
    product_name: str,
    image_path: Path,
) -> None:
    connection.execute(
        """
        INSERT INTO assets (style_code, product_name, image_path)
        VALUES (?, ?, ?)
        """,
        (style_code, product_name, image_path.as_posix()),
    )
    connection.commit()


def get_gallery_assets(
    connection: sqlite3.Connection,
    request: Request,
) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT style_code, product_name, image_path, created_at
        FROM assets
        ORDER BY datetime(created_at) DESC, created_at DESC
        """
    ).fetchall()

    assets: list[dict[str, str]] = []
    for style_code, product_name, image_path, created_at in rows:
        image_filename = Path(image_path).name
        assets.append(
            {
                "style_code": style_code,
                "product_name": product_name,
                "image_path": image_path,
                "image_url": str(
                    request.url_for("serve_image", image_name=image_filename)
                ),
                "created_at": created_at,
            }
        )
    return assets


@app.get("/static/images/{image_name}", name="serve_image")
def serve_image(image_name: str) -> FileResponse:
    image_path = IMAGE_DIR / Path(image_name).name
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        create_asset_table(connection)
        assets = get_gallery_assets(connection, request)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "assets": assets,
        },
    )


def safe_filename(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return normalized.strip("-._") or "nike-snkrs-image"


def image_extension(image_url: str) -> str:
    path = urllib.parse.urlparse(image_url).path
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return suffix
    return ".jpg"


def download_image(image_url: str, style_code: str, image_dir: Path, timeout: float) -> Path:
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{safe_filename(style_code)}{image_extension(image_url)}"

    request = urllib.request.Request(image_url, headers=IMAGE_REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            image_path.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Image download for {style_code} returned HTTP {exc.code}: {image_url}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Failed to download image for {style_code}: {exc.reason}"
        ) from exc

    return image_path


def save_new_assets(
    product_threads: Iterable[dict[str, Any]],
    connection: sqlite3.Connection,
    image_dir: Path,
    timeout: float,
) -> None:
    for thread in product_threads:
        product_name = extract_title(thread)
        style_codes = extract_style_colors(thread)
        image_urls = extract_node_image_urls(thread)

        if not style_codes:
            print(
                f"Skipping {product_name}: no style-color code found.",
                file=sys.stderr,
            )
            continue

        if not image_urls:
            print(
                f"Skipping {product_name}: no image URL found in thread nodes.",
                file=sys.stderr,
            )
            continue

        for style_code in style_codes:
            if asset_exists(connection, style_code):
                continue

            image_path = download_image(image_urls[0], style_code, image_dir, timeout)
            insert_asset(connection, style_code, product_name, image_path)
            print(f"NEW ASSET SAVED: {product_name}")


def main() -> int:
    args = parse_args()
    url = build_request_url(args)

    try:
        payload = fetch_json(url, args.timeout)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    product_threads = get_product_threads(payload)
    if not product_threads:
        print("No product threads found in the Nike API response.", file=sys.stderr)
        return 1

    database_path = Path(args.database)
    if database_path.parent != Path("."):
        database_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sqlite3.connect(database_path) as connection:
            create_asset_table(connection)
            save_new_assets(
                product_threads,
                connection,
                Path(args.image_dir),
                args.timeout,
            )
    except (RuntimeError, sqlite3.Error) as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
