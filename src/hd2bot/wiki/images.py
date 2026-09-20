"""Validate offline SVG illustrations before embedding them in a card image."""
import re
import xml.etree.ElementTree as ET

SVG_NAMESPACE = 'http://www.w3.org/2000/svg'
ALLOWED = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polygon', 'polyline',
           'defs', 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'mask', 'use',
           'symbol', 'title', 'desc', 'text', 'tspan', 'style'}
METADATA = {'metadata', 'namedview'}


def validated_svg(raw: bytes) -> bytes:
    if len(raw) > 512_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('unsupported_svg_document')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError('invalid_svg') from None
    if root.tag not in ('svg', '{'+SVG_NAMESPACE+'}svg'):
        raise ValueError('svg_root_required')
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit('}', 1)[-1] in METADATA:
                parent.remove(child)
    # ElementTree parses nested XML iteratively but serializes recursively. Bound
    # depth before tostring so malformed artwork cannot escape the text fallback.
    nodes = []
    pending = [(root, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > 128:
            raise ValueError('svg_depth_limit')
        nodes.append(node)
        if len(nodes) > 5000:
            raise ValueError('svg_node_limit')
        pending.extend((child, depth + 1) for child in node)
    for node in nodes:
        tag = node.tag.rsplit('}', 1)[-1]
        if tag not in ALLOWED:
            raise ValueError('unsupported_svg_element')
        values = []
        for name, value in node.attrib.items():
            local = name.rsplit('}', 1)[-1].lower()
            if local.startswith('on'):
                raise ValueError('svg_event_handler')
            if local in ('href', 'src') and not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*', value):
                raise ValueError('svg_external_reference')
            values.append(value)
        if tag == 'style':
            values.append(node.text or '')
        for value in values:
            if re.search(r'@import|@font-face|javascript:|data:|https?://|file:', value, re.I):
                raise ValueError('svg_external_reference')
            for target in re.findall(r'url\((.*?)\)', value, re.I):
                if not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*', target.strip(' \"\'')):
                    raise ValueError('svg_external_reference')
    ET.register_namespace('', SVG_NAMESPACE)
    return ET.tostring(root, encoding='utf-8')
