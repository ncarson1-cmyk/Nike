#!/usr/bin/env python3
"""Fetch Nike SNKRS product feed data and print selected product fields."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from typing import Any


DEFAULT_ENDPOINT = "https://api.nike.com/product_feed/rollup_threads/v2"
DEFAULT_SNKRS_CHANNEL_ID = "008be467-6c78-4079-94f0-70e2d6cc4003"
IMAGE_EXTENSIONS = (".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the Nike SNKRS product feed and print each thread's product "
            "title, style-color code, and image URLs from publishedContent.nodes."
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
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Origin": "https://www.nike.com",
            "Referer": "https://www.nike.com/launch",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
        },
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


def print_thread(index: int, thread: dict[str, Any]) -> None:
    title = extract_title(thread)
    style_colors = extract_style_colors(thread)
    image_urls = extract_node_image_urls(thread)

    print(f"Product thread #{index}")
    print(f"Product title: {title}")
    print(f"Style-color code: {', '.join(style_colors) if style_colors else 'N/A'}")
    print("Image URLs:")
    if image_urls:
        for image_url in image_urls:
            print(f"  - {image_url}")
    else:
        print("  - N/A")
    print()


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

    for index, thread in enumerate(product_threads, start=1):
        print_thread(index, thread)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
