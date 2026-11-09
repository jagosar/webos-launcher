"""plan-picture: an HTML page with an SVG of the manifest (packages by wave, their dependencies) and a table.

It is drawn without a model and without a script or an external resource, so the same manifest always gives the same
bytes. /architect shows it at the approval gate.
"""
import html

BOX_W = 180
BOX_H = 48
COL_GAP = 80
ROW_GAP = 24
MARGIN = 20
HEADER_H = 28


def layout(packages):
    """{id: (x, y)}: one column per wave (ascending), one row per id within it."""
    waves = sorted({wp.get("wave", 0) for wp in packages})
    spots = {}
    for col, wave in enumerate(waves):
        ids = sorted(wp["id"] for wp in packages if wp.get("wave", 0) == wave)
        for row, ident in enumerate(ids):
            spots[ident] = (MARGIN + col * (BOX_W + COL_GAP), MARGIN + HEADER_H + row * (BOX_H + ROW_GAP))
    return waves, spots


def svg(packages):
    waves, spots = layout(packages)
    rows = max([sum(1 for wp in packages if wp.get("wave", 0) == w) for w in waves] or [0])
    width = 2 * MARGIN + max(len(waves), 1) * (BOX_W + COL_GAP) - COL_GAP
    height = 2 * MARGIN + HEADER_H + rows * (BOX_H + ROW_GAP)
    out = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    for col, wave in enumerate(waves):
        x = MARGIN + col * (BOX_W + COL_GAP)
        out.append(f'<text class="wave" x="{x}" y="{MARGIN + 14}">Wave {html.escape(str(wave))}</text>')
    for wp in sorted(packages, key=lambda p: p["id"]):
        for dep in wp.get("depends_on") or []:
            if dep in spots:
                (x1, y1), (x2, y2) = spots[dep], spots[wp["id"]]
                out.append(f'<line class="dep" data-from="{html.escape(dep)}" data-to="{html.escape(wp["id"])}" '
                           f'x1="{x1 + BOX_W}" y1="{y1 + BOX_H // 2}" x2="{x2}" y2="{y2 + BOX_H // 2}"/>')
    for wp in sorted(packages, key=lambda p: p["id"]):
        x, y = spots[wp["id"]]
        ident = html.escape(wp["id"])
        sizes = f'{len(wp.get("files") or [])} files · {len(wp.get("acs") or [])} ACs'
        out.append(f'<g id="wp-{ident}"><rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6"/>'
                   f'<text x="{x + 10}" y="{y + 20}">{ident}</text>'
                   f'<text class="sizes" x="{x + 10}" y="{y + 38}">{html.escape(sizes)}</text></g>')
    out.append("</svg>")
    return "\n".join(out)


def table(packages):
    out = ["<table>", "<tr><th>WP</th><th>Wave</th><th>Depends on</th><th>Files</th><th>ACs</th></tr>"]
    for wp in sorted(packages, key=lambda p: (p.get("wave", 0), p["id"])):
        cells = [wp["id"], str(wp.get("wave", "")), ", ".join(wp.get("depends_on") or []),
                 ", ".join(wp.get("files") or []), ", ".join(wp.get("acs") or [])]
        out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


STYLE = """body { font-family: system-ui, sans-serif; margin: 16px; background: #fff; color: #1a1a1a; }
rect { fill: #eef2f7; stroke: #4a6585; }
text { font-size: 13px; fill: #1a1a1a; }
text.wave { font-weight: 600; }
text.sizes { font-size: 11px; fill: #555; }
line.dep { stroke: #4a6585; stroke-width: 1.5; }
table { border-collapse: collapse; margin-top: 16px; font-size: 13px; }
td, th { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }
@media (prefers-color-scheme: dark) {
  body { background: #161616; color: #e6e6e6; }
  rect { fill: #24303d; stroke: #8fa9c6; }
  text { fill: #e6e6e6; }
  text.sizes { fill: #aaa; }
  line.dep { stroke: #8fa9c6; }
  td, th { border-color: #444; }
}"""


def render(manifest, title="Plan"):
    """A whole HTML document with one inline <svg> and one <table>; the same manifest and title give the same
    string."""
    packages = [wp for wp in manifest.get("work_packages") or [] if isinstance(wp, dict) and "id" in wp]
    name = html.escape(title)
    return "\n".join(["<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
                      '<meta name="viewport" content="width=device-width, initial-scale=1">',
                      f"<title>{name}</title>", f"<style>\n{STYLE}\n</style>", "</head>", "<body>",
                      f"<h1>{name}</h1>", svg(packages), table(packages), "</body>", "</html>", ""])
