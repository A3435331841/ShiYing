from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import main


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "app" / "static"
ARTWORK_ROOT = STATIC_ROOT / "platform-icons"
NODE_EXECUTION_TIMEOUT_SECONDS = 30
PLATFORM_ARTWORK = {
    "douyin": ("抖音", "douyin.png", "image/png"),
    "selected-douyin": ("抖音精选", "selected-douyin.png", "image/png"),
    "kuaishou": ("快手", "kuaishou.png", "image/png"),
    "wechat": ("视频号", "wechat.svg", "image/svg+xml"),
    "xiaohongshu": ("小红书", "xiaohongshu.png", "image/png"),
    "bilibili": ("B站", "bilibili.jpg", "image/jpeg"),
    "youtube": ("YouTube", "youtube.png", "image/png"),
}
EXPECTED_PUBLISHERS = {
    "douyin": "Beijing Douyin Technology Co., Ltd.",
    "selected-douyin": "Beijing Douyin Technology Co., Ltd.",
    "kuaishou": "Beijing Kwai Technology Co., Ltd.",
    "wechat": "Tencent",
    "xiaohongshu": "Xingin Information Technology (Shanghai) Co., Ltd",
    "bilibili": "bilibili",
    "youtube": "Google LLC",
}
APP_STORE_SOURCE_HOSTS = {
    "source_page": "apps.apple.com",
    "metadata_url": "itunes.apple.com",
    "metadata_artwork_url": "is1-ssl.mzstatic.com",
    "asset_url": "is1-ssl.mzstatic.com",
    "resolved_asset_url": "is1-ssl.mzstatic.com",
}
WECHAT_PATH_SHA256 = (
    "7b079653468e1dc230cabd53b78b240f5cc4134092adcdf2cfb5f8747e25ea28"
)


class _MarkupParser(HTMLParser):
    """Collect attributes and parentage without depending on attribute order."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__()
        self.elements = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        element = {
            "tag": tag,
            "attrs": dict(attrs),
            "parent": self._stack[-1] if self._stack else None,
            "text": "",
        }
        self.elements.append(element)
        if tag not in self.VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data):
        if self._stack:
            self._stack[-1]["text"] += data


def _parse_markup(source):
    parser = _MarkupParser()
    parser.feed(source)
    parser.close()
    return parser.elements


def _has_class(element, name):
    return name in element["attrs"].get("class", "").split()


@pytest.fixture
def artwork_sources():
    records = json.loads((ARTWORK_ROOT / "sources.json").read_text(encoding="utf-8"))
    assert len(records) == len(PLATFORM_ARTWORK)
    assert {record["key"] for record in records} == set(PLATFORM_ARTWORK)
    return {record["key"]: record for record in records}


def test_index_platform_chips_use_seven_local_official_images():
    elements = _parse_markup((STATIC_ROOT / "index.html").read_text(encoding="utf-8"))
    rows = [element for element in elements if _has_class(element, "platform-row")]
    assert len(rows) == 1
    chips = [element for element in elements if _has_class(element, "platform-chip")]
    assert len(chips) == len(PLATFORM_ARTWORK)
    images = [element for element in elements if _has_class(element, "platform-icon")]
    assert len(images) == len(chips)
    expected_paths = {
        f"/static/platform-icons/{filename}"
        for _, filename, _ in PLATFORM_ARTWORK.values()
    }
    actual_paths = []

    for chip in chips:
        assert chip["parent"] is rows[0]
        assert chip["text"].strip()  # Decorative images retain visible platform names.
        children = [element for element in elements if element["parent"] is chip]
        assert len(children) == 1
        image = children[0]
        assert image["tag"] == "img"
        assert _has_class(image, "platform-icon")
        path = image["attrs"]["src"]
        assert path in expected_paths
        actual_paths.append(path)
        assert (ARTWORK_ROOT / path.rsplit("/", 1)[1]).is_file()
        for name, value in {"alt": "", "width": "22", "height": "22", "aria-hidden": "true"}.items():
            assert image["attrs"][name] == value
        assert not any(name.startswith("on") for name in image["attrs"])
    assert set(actual_paths) == expected_paths

    assert not any(
        element["attrs"].get("id", "").startswith("p-")
        or element["attrs"].get("href", "").startswith("#p-")
        for element in elements
    )


def test_platform_artwork_css_preserves_complete_unfiltered_images():
    styles = (STATIC_ROOT / "home.css").read_text(encoding="utf-8")
    styles = re.sub(r"/\*.*?\*/", "", styles, flags=re.DOTALL)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", styles)
    shared_rules = [
        body
        for selectors, body in rules
        if {".platform-icon", ".thumb-platform-icon"}.issubset(
            {selector.strip() for selector in selectors.split(",")}
        )
    ]
    assert len(shared_rules) == 1
    properties = dict(
        (name.strip(), value.strip())
        for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", shared_rules[0])
    )
    expected = {
        "width": "22px",
        "height": "22px",
        "object-fit": "contain",
        "object-position": "center",
        "padding": "0",
        "background": "transparent",
        "opacity": "1",
        "filter": "none",
    }
    for name, value in expected.items():
        assert properties[name] == value

    # A later per-platform rule must not recolor, crop, or fade official artwork.
    for selectors, body in rules:
        if not re.search(r"\.(?:platform-icon|thumb-platform-icon)\b", selectors):
            continue
        declarations = re.findall(r"([\w-]+)\s*:\s*([^;]+)", body)
        for name, value in declarations:
            assert not name.startswith("stroke")
            if name in {"object-fit", "padding", "opacity", "filter"}:
                assert value.strip() == expected[name]


@pytest.mark.parametrize("key", PLATFORM_ARTWORK)
def test_local_artwork_is_served_with_recorded_bytes_and_image_type(key, artwork_sources):
    _, filename, content_type = PLATFORM_ARTWORK[key]
    record = artwork_sources[key]
    client = TestClient(main.app, base_url="http://127.0.0.1")
    response = client.get(f"/static/platform-icons/{filename}")

    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == content_type
    assert response.content == (ARTWORK_ROOT / filename).read_bytes()
    assert record["file"] == filename
    assert re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
    assert hashlib.sha256(response.content).hexdigest() == record["sha256"]

    if filename.endswith(".png"):
        assert record["format"] == "PNG"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert response.content[8:16] == b"\x00\x00\x00\rIHDR"
        dimensions = struct.unpack(">II", response.content[16:24])
        assert dimensions == tuple(record["size"]) == (512, 512)
    elif filename.endswith(".jpg"):
        assert record["format"] == "JPEG"
        assert response.content.startswith(b"\xff\xd8\xff")
        assert response.content.endswith(b"\xff\xd9")
        # The SHA pins the original JPEG; its source metadata records its size.
        assert record["size"] == [750, 750]
    else:
        assert record["format"] == "SVG"
        assert ET.fromstring(response.content).tag == "{http://www.w3.org/2000/svg}svg"


@pytest.mark.parametrize("key", PLATFORM_ARTWORK)
def test_artwork_provenance_uses_official_https_hosts_and_publishers(key, artwork_sources):
    record = artwork_sources[key]
    assert record["publisher"] == EXPECTED_PUBLISHERS[key]
    assert record["name"].strip()
    if key == "bilibili":
        expected_hosts = {
            "source_page": "app.bilibili.com",
            "asset_url": "i0.hdslb.com",
        }
        assert record["source_element"] == "img.icon"
    elif key == "wechat":
        expected_hosts = {
            "source_page": "channels.weixin.qq.com",
            "asset_url": "res.wx.qq.com",
            "source_color_url": "res.wx.qq.com",
        }
        assert record["source_symbol"] == "icon-logo"
        assert re.fullmatch(r"[0-9a-f]{64}", record["source_bundle_sha256"])
    else:
        expected_hosts = APP_STORE_SOURCE_HOSTS

    url_fields = {
        name for name in record if name.endswith("_url") or name == "source_page"
    }
    assert url_fields == set(expected_hosts)
    for name, host in expected_hosts.items():
        url = urlsplit(record[name])
        assert url.scheme == "https"
        assert url.hostname == host
        assert url.port in {None, 443}
        assert url.username is None and url.password is None
        assert not url.fragment
    if key != "wechat":
        assert record["transformations"] == "none; original response bytes"


def test_wechat_svg_keeps_the_native_single_path_without_active_content(artwork_sources):
    record = artwork_sources["wechat"]
    source = (ARTWORK_ROOT / "wechat.svg").read_bytes()
    assert not re.search(rb"<!DOCTYPE\b|<!ENTITY\b|<\?xml-stylesheet\b", source, re.IGNORECASE)
    root = ET.fromstring(source)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert set(root.attrib) == {"viewBox", "fill"}
    assert root.attrib["viewBox"] == record["source_viewbox"] == "0 0 32 32"
    assert root.attrib["fill"] == record["fill"] == "#fa9d3b"
    assert len(root) == 1
    path = root[0]
    assert path.tag == "{http://www.w3.org/2000/svg}path"
    assert len(path) == 0
    # Strict tag/attribute allowlists exclude scripts, events, styles, and hrefs.
    assert set(path.attrib) == {"d", "fill-rule"}
    assert path.attrib["fill-rule"] == "evenodd"
    for element in (root, path):
        assert not (element.text or "").strip()
        assert not (element.tail or "").strip()
    path_data = path.attrib["d"]
    assert re.fullmatch(r"[MmZzLlHhVvCcSsQqTtAa0-9eE+.,\s-]+", path_data)
    assert hashlib.sha256(path_data.encode("utf-8")).hexdigest() == (
        record["source_path_sha256"]
    ) == WECHAT_PATH_SHA256


def test_douyin_and_selected_douyin_have_distinct_official_artwork(artwork_sources):
    douyin = artwork_sources["douyin"]
    selected = artwork_sources["selected-douyin"]
    assert douyin["source_page"] != selected["source_page"]
    assert douyin["asset_url"] != selected["asset_url"]
    assert douyin["sha256"] != selected["sha256"]
    assert (ARTWORK_ROOT / douyin["file"]).read_bytes() != (
        ARTWORK_ROOT / selected["file"]
    ).read_bytes()


def _run_home_icon_script(tmp_path: Path, filename: str, script: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    source = (STATIC_ROOT / "home.js").read_text(encoding="utf-8")
    marker = "  initialize();\n})();\n"
    assert source.count(marker) == 1
    source = source.replace(
        marker,
        "  window.__artwork = { canonicalPlatform, platformIcon, platforms };\n})();\n",
    )
    harness = (
        'const vm = require("node:vm");\n'
        "const context = { window: {} };\n"
        f"vm.runInNewContext({json.dumps(source)}, context, {{ timeout: 1000 }});\n"
        "const helpers = context.window.__artwork;\n"
    )
    script_path = tmp_path / filename
    script_path.write_text(harness + script, encoding="utf-8")
    completed = subprocess.run(
        [node, str(script_path)],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=NODE_EXECUTION_TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_platform_helpers_render_all_canonical_keys_and_aliases(tmp_path: Path):
    aliases = {"xhs": "xiaohongshu", "bili": "bilibili"}
    inputs = list(PLATFORM_ARTWORK) + list(aliases)
    output = _run_home_icon_script(
        tmp_path,
        "platform-artwork-known.js",
        f"const inputs = {json.dumps(inputs)};\n"
        "process.stdout.write(JSON.stringify({\n"
        "  labels: helpers.platforms,\n"
        "  cases: inputs.map(value => ({\n"
        "    canonical: helpers.canonicalPlatform(value),\n"
        "    markup: helpers.platformIcon(value)\n"
        "  }))\n"
        "}));\n",
    )
    assert output["labels"] == {
        key: label for key, (label, _, _) in PLATFORM_ARTWORK.items()
    }
    assert len(output["cases"]) == len(inputs)
    for value, result in zip(inputs, output["cases"], strict=True):
        canonical = aliases.get(value, value)
        assert result["canonical"] == canonical
        elements = _parse_markup(result["markup"])
        assert len(elements) == 1
        image = elements[0]
        assert image["tag"] == "img"
        assert image["attrs"] == {
            "class": "thumb-platform-icon",
            "src": f"/static/platform-icons/{PLATFORM_ARTWORK[canonical][1]}",
            "alt": "",
            "width": "22",
            "height": "22",
            "aria-hidden": "true",
        }


def test_platform_helpers_never_use_unknown_or_malicious_values_as_image_sources(tmp_path: Path):
    unsafe_values = [
        "unknown", "", "constructor", "__proto__", "toString", "hasOwnProperty",
        "douyin.png", "wechat.svg", "../../private", "xhs/../../private",
        "https://evil.example/icon.png", "//evil.example/icon.png",
        "javascript:alert(1)", "data:image/svg+xml,<svg onload=alert(1)>",
        'douyin" onerror="alert(1)', '"><img src=x onerror=alert(1)>',
        '<svg onload="alert(1)">', "douyin\x00", None, False, 0, [], {},
    ]
    output = _run_home_icon_script(
        tmp_path,
        "platform-artwork-unsafe.js",
        f"const inputs = {json.dumps(unsafe_values)};\n"
        "inputs.push(undefined);\n"
        "process.stdout.write(JSON.stringify([\n"
        "  ...inputs.map(value => helpers.platformIcon(value)),\n"
        "  helpers.platformIcon()\n"
        "]));\n",
    )
    assert len(output) == len(unsafe_values) + 2
    for markup in output:
        assert markup == '<svg aria-hidden="true"><use href="#i-film"/></svg>'
        elements = _parse_markup(markup)
        assert [element["tag"] for element in elements] == ["svg", "use"]
        assert elements[1]["attrs"] == {"href": "#i-film"}
        assert not any("src" in element["attrs"] for element in elements)
