"""Testes do /img (miniaturas WebP) com o CDN mockado em memoria."""
import io
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

U = "https://cdn.gymrats.app/0b6f1c3e-uuid.jpg"


def jpeg(w=1400, h=1889):
    im = Image.new("RGB", (w, h))
    # gradiente + listras: conteudo realista p/ o encoder (nao comprime a quase nada)
    px = im.load()
    for y in range(0, h, 1):
        for x in range(0, w, 7):
            px[x, y] = (x * 255 // w, y * 255 // h, ((x // 7 + y // 9) % 2) * 255)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


class FakeResp:
    def __init__(self, body=b"", status=200, ctype="image/jpeg", clen=True):
        self.status_code, self.body = status, body
        self.headers = {"Content-Type": ctype}
        if clen:
            self.headers["Content-Length"] = str(len(body))

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


@pytest.fixture(scope="module")
def big():
    return jpeg()


@pytest.fixture
def api_mod(tmp_path, monkeypatch):
    # import tardio: test_insights define GYMRATS_DB antes de importar a api na coleta
    import api
    monkeypatch.setenv("GYMRATS_IMG_CACHE", str(tmp_path / "thumbs"))
    return api


@pytest.fixture
def upstream(api_mod, monkeypatch, big):
    """Troca requests.get; .resp define a resposta, .calls conta chamadas."""
    class Up:
        resp = None
        calls = []

    up = Up()
    up.resp = FakeResp(big)
    up.calls = []

    def fake_get(url, **kw):
        up.calls.append((url, kw))
        return up.resp

    monkeypatch.setattr(api_mod.requests, "get", fake_get)
    return up


@pytest.fixture
def client(api_mod):
    from fastapi.testclient import TestClient
    return TestClient(api_mod.app)


def get(client, u=U, w=128, **headers):
    return client.get("/img", params={"u": u, "w": w}, headers=headers)


@pytest.mark.parametrize("u", [
    "https://evil.com/x.jpg",
    "http://cdn.gymrats.app/x.jpg",
    "https://cdn.gymrats.app.evil.com/x.jpg",
    "https://a@cdn.gymrats.app/x",
    "https://cdn.gymrats.app:8443/x.jpg",
    "https://cdn.gymrats.app/",
    "https://cdn.gymrats.app/x.jpg?v=1",
    "file:///etc/passwd",
    "cdn.gymrats.app/x.jpg",
])
def test_bad_url_400(client, upstream, u):
    r = get(client, u=u)
    assert r.status_code == 400
    assert upstream.calls == []


@pytest.mark.parametrize("w", [0, 32, 100, 2048, -64])
def test_bad_width_400(client, upstream, w):
    assert get(client, w=w).status_code == 400
    assert upstream.calls == []


def test_missing_params_422(client, upstream):
    assert client.get("/img").status_code == 422


def test_public_even_with_api_key(client, upstream, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "API_KEY", "segredo")
    assert get(client).status_code == 200


@pytest.mark.parametrize("w", [64, 128, 512])
def test_resize_webp(client, upstream, w):
    r = get(client, w=w)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    im = Image.open(io.BytesIO(r.content))
    assert im.format == "WEBP" and im.width == w
    assert im.height == round(1889 * w / 1400)
    url, kw = upstream.calls[0]
    assert url == U and kw["timeout"] == 15 and kw["stream"] and kw["allow_redirects"] is False


def test_no_upscale(client, upstream):
    upstream.resp = FakeResp(jpeg(50, 80))
    im = Image.open(io.BytesIO(get(client, w=256).content))
    assert im.size == (50, 80)


def test_exif_orientation(client, upstream):
    src = Image.new("RGB", (300, 100), "red")
    ex = Image.Exif()
    ex[0x0112] = 6  # rotacionar 90 graus
    out = io.BytesIO()
    src.save(out, "JPEG", exif=ex)
    upstream.resp = FakeResp(out.getvalue())
    im = Image.open(io.BytesIO(get(client, w=64).content))
    assert im.size == (64, 192)


def test_keeps_alpha(client, upstream):
    out = io.BytesIO()
    Image.new("RGBA", (200, 200), (0, 0, 255, 0)).save(out, "PNG")
    upstream.resp = FakeResp(out.getvalue(), ctype="image/png")
    im = Image.open(io.BytesIO(get(client, w=64).content))
    assert im.mode == "RGBA" and im.getpixel((10, 10))[3] == 0


def test_cache_hit(client, upstream, tmp_path):
    a = get(client)
    b = get(client)
    assert a.status_code == b.status_code == 200 and a.content == b.content
    assert len(upstream.calls) == 1
    files = os.listdir(tmp_path / "thumbs")
    assert len(files) == 1 and files[0].endswith("_128.webp")  # sem .tmp sobrando
    get(client, w=64)  # outra largura -> outra entrada
    assert len(upstream.calls) == 2


def test_etag_304(client, upstream):
    r = get(client)
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('_128"')
    r2 = get(client, **{"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b""
    assert r2.headers["etag"] == etag
    assert r2.headers["cache-control"].startswith("public")
    assert get(client, **{"If-None-Match": f'"x", W/{etag}'}).status_code == 304
    assert get(client, **{"If-None-Match": '"outro"'}).status_code == 200
    assert get(client, w=64, **{"If-None-Match": etag}).status_code == 200  # etag por largura


def test_upstream_404(client, upstream, tmp_path):
    upstream.resp = FakeResp(b"nope", status=404, ctype="text/html")
    assert get(client).status_code == 404
    assert os.listdir(tmp_path / "thumbs") == []  # erro nao e cacheado


@pytest.mark.parametrize("status", [500, 301, 403])
def test_upstream_error_502(client, upstream, status):
    upstream.resp = FakeResp(b"", status=status)
    assert get(client).status_code == 502


def test_not_image_502(client, upstream):
    upstream.resp = FakeResp(b"<html>", ctype="text/html")
    assert get(client).status_code == 502


def test_garbage_image_502(client, upstream):
    upstream.resp = FakeResp(b"nao sou jpeg")
    assert get(client).status_code == 502


def test_oversize_content_length_502(client, upstream, api_mod):
    upstream.resp = FakeResp(b"x")
    upstream.resp.headers["Content-Length"] = str(api_mod.IMG_MAX_BYTES + 1)
    assert get(client).status_code == 502


def test_oversize_streamed_502(client, upstream, api_mod, monkeypatch):
    # sem Content-Length (chunked): corta ao passar do limite enquanto le
    monkeypatch.setattr(api_mod, "IMG_MAX_BYTES", 1000)
    upstream.resp = FakeResp(b"x" * 5000, clen=False)
    assert get(client).status_code == 502


def test_decompression_bomb_502(client, upstream, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # 1400x1889 vira "bomba"
    assert get(client).status_code == 502
