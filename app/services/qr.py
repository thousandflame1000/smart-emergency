# -*- coding: utf-8 -*-
"""QR code as an inline SVG data URI, so the console can show it without another request."""
import base64
import io

import segno


def svg_data_uri(text: str) -> str:
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg", scale=6, border=2, xmldecl=False, svgns=True)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()
