"""Non-régression : le proxy d'images refuse le SVG quelle que soit la casse.

Le type n'était pas mis en minuscules : `image/SVG+xml` passait le filtre et
était servi depuis notre origine, alors que le navigateur le lit comme un SVG.
"""
import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.routes.links import net_guard, proxy


class _Req:
    headers = {}


@pytest.mark.parametrize("ctype", ["image/svg+xml", "image/SVG+xml", "Image/Svg+Xml; charset=utf-8"])
def test_svg_refuse_quelle_que_soit_la_casse(monkeypatch, ctype):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

    async def _public(url):
        return True

    monkeypatch.setattr(proxy, "_assert_public_url", _public)
    monkeypatch.setattr(net_guard, "_assert_public_url", _public)

    async def _run():
        transport = httpx.MockTransport(lambda req: httpx.Response(200, headers={"content-type": ctype}, content=svg))
        async with httpx.AsyncClient(transport=transport) as client:
            original = net_guard._http_client
            net_guard.set_http_client(client)
            try:
                await proxy.proxy_image(request=_Req(), url=f"https://img.example/{hash(ctype)}.svg",
                                        user=None, session=type("S", (), {"close": lambda self: None})())
            finally:
                net_guard.set_http_client(original)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_run())
    assert exc.value.status_code == 415
