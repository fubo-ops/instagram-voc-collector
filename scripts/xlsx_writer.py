"""Small dependency-free XLSX writer for the collector's fixed audit workbook."""

from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED


def _col(index):
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _cell(value, ref, header=False, wrap=False):
    style = ' s="1"' if header else (' s="2"' if wrap else "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, style, value)
    value = "" if value is None else str(value)
    # Inline strings avoid a shared-string table and preserve arbitrary Unicode.
    return '<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style, escape(value)
    )


def write_workbook(path, sheets):
    """Write ordered (sheet_name, headers, rows) tuples to a valid XLSX."""
    names = [name for name, _, _ in sheets]
    if len(names) != len(set(names)) or any(len(name) > 31 for name in names):
        raise ValueError("Sheet names must be unique and at most 31 characters")
    types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>']
    overrides = ['<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                 '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for i in range(1, len(sheets) + 1):
        overrides.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i)
    workbook_sheets = ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(name), i, i) for i, name in enumerate(names, 1))
    workbook = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>%s</sheets></workbook>') % workbook_sheets
    rels = ''.join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, i) for i in range(1, len(sheets) + 1))
    rels += '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (len(sheets) + 1)
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font/><font><b/></font></fonts>'
              '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf></cellXfs>'
              '</styleSheet>')
    with ZipFile(str(path), 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">%s%s</Types>' % (''.join(types), ''.join(overrides)))
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', workbook)
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % rels)
        z.writestr('xl/styles.xml', styles)
        for i, (_, headers, rows) in enumerate(sheets, 1):
            data = [headers] + list(rows)
            row_xml = []
            for row_num, row in enumerate(data, 1):
                cells = ''.join(_cell(value, '%s%d' % (_col(col_num), row_num), row_num == 1, row_num > 1 and headers[col_num - 1] in ('comment_text', 'reason')) for col_num, value in enumerate(row, 1))
                row_xml.append('<row r="%d">%s</row>' % (row_num, cells))
            last = '%s%d' % (_col(len(headers)), max(1, len(data)))
            widths = ''.join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (j, j, min(60, max(12, len(str(h)) + 4))) for j, h in enumerate(headers, 1))
            sheet = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                     '<dimension ref="A1:%s"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                     '<cols>%s</cols><sheetData>%s</sheetData><autoFilter ref="A1:%s"/></worksheet>') % (last, widths, ''.join(row_xml), last)
            z.writestr('xl/worksheets/sheet%d.xml' % i, sheet)
