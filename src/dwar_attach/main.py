#!/usr/bin/env python3
import sys
import shutil
import re
from collections import Counter
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="Add image support to DataWarrior .dwar files.")

SUPPORTED_EXTENSIONS = ['.png', '.jpg', '.jpeg']

MIME_TYPES = {
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
}


def find_image(image_dir: Path, id_val):
    """Return (filename, mime_type) for the first matching image file, or (None, None)."""
    for ext in SUPPORTED_EXTENSIONS:
        filename = id_val + ext
        if (image_dir / filename).is_file():
            return filename, MIME_TYPES[ext]
        # Case-insensitive fallback
        filename_upper = id_val + ext.upper()
        if (image_dir / filename_upper).is_file():
            return filename_upper, MIME_TYPES[ext]
    return None, None


def encode_dw(data: bytes) -> str:
    """Encode binary data to DataWarrior's 6-bit format with 4-byte length prefix."""
    payload = len(data).to_bytes(4, 'big') + data
    pad = (-len(payload) * 8) % 6
    bits = int.from_bytes(payload, 'big') << pad
    n = (len(payload) * 8 + pad) // 6
    chars = [chr(((bits >> (6 * (n - 1 - i))) & 0x3F) + 64) for i in range(n)]
    s = ''.join(chars)
    return '\n'.join(s[i:i+80] for i in range(0, len(s), 80))


def _column_block(column_props: str, image_column: str) -> str | None:
    """Return the text of the column block for image_column, or None if not found."""
    m = re.search(
        rf'<columnName="{re.escape(image_column)}">.*?(?=<columnName=|</column properties>)',
        column_props, re.DOTALL
    )
    return m.group() if m else None


def _slot_props(slot: int, source: str, mime_type: str, name: str) -> str:
    """Return the three detailSource/Type/Name property lines for a slot."""
    return (
        f'<columnProperty="detailSource{slot}\t{source}">\n'
        f'<columnProperty="detailType{slot}\t{mime_type}">\n'
        f'<columnProperty="detailName{slot}\t{name}">\n'
    )


def get_detail_count(column_props: str, image_column: str) -> int:
    """Return the current detailCount for image_column, or 0 if not found."""
    block = _column_block(column_props, image_column)
    if not block:
        return 0
    m = re.search(r'<columnProperty="detailCount\t(\d+)">', block)
    return int(m.group(1)) if m else 0


def count_existing_details(detail_data: str | None) -> int:
    """Count <detailID=...> entries already present in the detail data block."""
    if not detail_data:
        return 0
    return len(re.findall(r'<detailID="\d+">', detail_data))


def build_detail_section(details: list, linesep: str, existing: str | None = None) -> str:
    """Build the <detail data> block from a list of raw image bytes.

    If existing is provided, new entries are appended after the existing ones.
    """
    lines = ['<detail data>']
    start_id = 1
    if existing:
        inner = re.sub(r'^<detail data>\s*|\s*</detail data>$', '', existing)
        start_id = len(re.findall(r'<detailID="\d+">', inner)) + 1
        lines.append(inner)
    for i, data in enumerate(details, start=start_id):
        lines.append(f'<detailID="{i}">')
        lines.append(encode_dw(data))
        lines.append(f'</detailID>')
    lines.append('</detail data>')
    return linesep.join(lines)


def parse_dwar(content):
    """Parse a .dwar file into sections (fileinfo, column_props, data, hitlist, detail_data, properties)."""
    sections = {}

    m = re.search(r'(<datawarrior-fileinfo>.*?</datawarrior-fileinfo>)', content, re.DOTALL)
    sections['fileinfo'] = m.group(1) if m else ''
    after_fileinfo = content[m.end():] if m else content

    m = re.search(r'(<column properties>.*?</column properties>)', after_fileinfo, re.DOTALL)
    if m:
        sections['column_props'] = m.group(1)
        after_colprops = after_fileinfo[m.end():]
    else:
        sections['column_props'] = None
        after_colprops = after_fileinfo

    m_hit = re.search(r'(<hitlist data>.*?</hitlist data>)', after_colprops, re.DOTALL)
    m_detail = re.search(r'(<detail data>.*?</detail data>)', after_colprops, re.DOTALL)
    m_prop = re.search(r'(<datawarrior properties>.*?</datawarrior properties>)', after_colprops, re.DOTALL)

    first_marker_pos = len(after_colprops)
    if m_hit:
        first_marker_pos = min(first_marker_pos, m_hit.start())
    if m_detail:
        first_marker_pos = min(first_marker_pos, m_detail.start())
    if m_prop:
        first_marker_pos = min(first_marker_pos, m_prop.start())

    sections['data'] = after_colprops[:first_marker_pos]
    sections['hitlist'] = m_hit.group(1) if m_hit else None
    sections['detail_data'] = m_detail.group(1) if m_detail else None
    sections['properties'] = m_prop.group(1) if m_prop else None

    last_end = 0
    if m_prop:
        last_end = content.index(m_prop.group(1)) + len(m_prop.group(1))
    elif m_detail:
        last_end = content.index(m_detail.group(1)) + len(m_detail.group(1))
    elif m_hit:
        last_end = content.index(m_hit.group(1)) + len(m_hit.group(1))
    sections['trailing'] = content[last_end:] if last_end else ''

    return sections


def detect_line_ending(content):
    return '\r\n' if '\r\n' in content else '\n'


def build_column_props_block(image_column, source, mime_type, slot: int = 0, name: str = ''):
    lines = [
        '<column properties>',
        f'<columnName="{image_column}">',
        f'<columnProperty="detailCount\t{slot + 1}">',
    ]
    lines.append(_slot_props(slot, source, mime_type, name or image_column).rstrip('\n'))
    lines.append('</column properties>')
    return '\n'.join(lines)


def update_column_props(existing, image_column, source, mime_type, slot: int = 0, name: str = ''):
    """Update or insert column properties for image_column in an existing block."""
    slot_name = name or image_column
    if f'<columnName="{image_column}">' in existing:
        # Increment existing detailCount by 1
        updated = re.sub(
            rf'(<columnProperty="detailCount\t)(\d+)(">)',
            lambda m: f'{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}',
            existing,
        )
        # Append new slot properties at the end of the column's block
        col_block_pattern = rf'(<columnName="{re.escape(image_column)}">.*?)(?=<columnName=|</column properties>)'
        return re.sub(
            col_block_pattern,
            lambda m: m.group(1) + _slot_props(slot, source, mime_type, slot_name),
            updated,
            flags=re.DOTALL,
        )
    else:
        new_props = (
            f'<columnName="{image_column}">\n'
            f'<columnProperty="detailCount\t{slot + 1}">\n'
            + _slot_props(slot, source, mime_type, slot_name)
        )
        return existing.replace('</column properties>', new_props + '</column properties>')


def compute_rel_path(dwar_path: Path, image_dir: Path):
    """Return the relPath value for DataWarrior's detailSource0 column property.
    This is the path from the .dwar file's directory to image_dir, with forward
    slashes and a trailing slash (e.g. 'images/'). Returns empty string if they
    are the same directory."""
    dwar_dir = dwar_path.resolve().parent
    rel = image_dir.resolve().relative_to(dwar_dir, walk_up=True)
    if str(rel) == '.':
        return ''
    return rel.as_posix() + '/'


def add_images(dwar_file: Path, id_column, image_column, image_dir: Optional[Path] = None, embed: bool = False, name: Optional[str] = None):
    if image_dir is None:
        image_dir = dwar_file.resolve().parent / 'images'

    if not image_dir.is_dir():
        print(f"Error: image directory not found: {image_dir}", file=sys.stderr)
        sys.exit(1)

    with open(dwar_file, 'r', encoding='utf-8', newline='') as f:
        content = f.read()

    linesep = detect_line_ending(content)
    sections = parse_dwar(content)

    data_text = sections['data'].strip('\r\n')
    data_lines = data_text.splitlines()
    if not data_lines:
        print("Error: no data found in file", file=sys.stderr)
        sys.exit(1)

    header = data_lines[0].split('\t')
    try:
        id_idx = header.index(id_column)
    except ValueError:
        print(f"Error: id-column '{id_column}' not found. Columns: {header}", file=sys.stderr)
        sys.exit(1)
    try:
        img_idx = header.index(image_column)
    except ValueError:
        print(f"Error: image-column '{image_column}' not found. Columns: {header}", file=sys.stderr)
        sys.exit(1)

    rel_path = compute_rel_path(dwar_file, image_dir)

    # Determine slot index and starting detail ID for this run
    slot = get_detail_count(sections['column_props'], image_column) if sections['column_props'] else 0
    next_id = count_existing_details(sections['detail_data']) + 1

    # Process rows
    missing = []
    mime_counts = Counter()
    new_data_lines = [data_lines[0]]
    embedded_images = []  # list of bytes for embed mode

    for line in data_lines[1:]:
        cols = line.split('\t')
        while len(cols) <= max(id_idx, img_idx):
            cols.append('')

        id_val = cols[id_idx].split('|#|')[0]
        filename, mime_type = find_image(image_dir, id_val)

        if filename:
            cell = cols[img_idx]
            display = cell.split('|#|')[0] if '|#|' in cell else cell
            # Preserve existing refs for other slots; replace/remove ref for current slot
            existing_refs = re.findall(r'\|#\|(\d+:[^|]+)', cell)
            kept_refs = [r for r in existing_refs if not r.startswith(f'{slot}:')]
            if embed:
                detail_id = next_id + len(embedded_images)
                image_bytes = (image_dir / filename).read_bytes()
                embedded_images.append(image_bytes)
                new_ref = f'{slot}:{detail_id}'
            else:
                new_ref = f'{slot}:{filename}'
            all_refs = kept_refs + [new_ref]
            cols[img_idx] = display + ''.join(f'|#|{r}' for r in all_refs)
            mime_counts[mime_type] += 1
        else:
            missing.append(str(image_dir / (id_val + '.<ext>')))

        new_data_lines.append('\t'.join(cols))

    if missing:
        print(f"Warning: no image found for {len(missing)} row(s):", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)

    if not mime_counts:
        print("Error: no images matched any rows.", file=sys.stderr)
        sys.exit(1)

    # Use the most common MIME type for the column property
    mime_type = mime_counts.most_common(1)[0][0]
    if len(mime_counts) > 1:
        print(f"Warning: mixed image types found {dict(mime_counts)}, using '{mime_type}' for column property.")

    new_data = linesep + linesep.join(new_data_lines) + linesep

    slot_name = name if name else image_column
    source = 'embedded' if embed else f'relPath:{rel_path}'
    if sections['column_props'] is None:
        new_col_props = build_column_props_block(image_column, source, mime_type, slot=slot, name=slot_name)
    else:
        new_col_props = update_column_props(sections['column_props'], image_column, source, mime_type, slot=slot, name=slot_name)

    parts = [sections['fileinfo'], linesep, new_col_props, new_data]
    if sections['hitlist']:
        parts.append(sections['hitlist'])
        parts.append(linesep)
    if embed and embedded_images:
        parts.append(build_detail_section(embedded_images, linesep, existing=sections['detail_data']))
        parts.append(linesep)
    elif sections['detail_data']:
        parts.append(sections['detail_data'])
        parts.append(linesep)
    if sections['properties']:
        parts.append(sections['properties'])
        parts.append(linesep)

    new_content = ''.join(parts)

    backup = dwar_file.with_name(dwar_file.name + '.bak')
    shutil.copy(dwar_file, backup)
    print(f"Backup: {backup}")

    with open(dwar_file, 'w', encoding='utf-8', newline='') as f:
        f.write(new_content)

    rows_updated = sum(mime_counts.values())
    print(f"Updated: {dwar_file}")
    print(f"  id-column:    {id_column} (index {id_idx})")
    print(f"  image-column: {image_column} (index {img_idx})")
    print(f"  image-dir:    {image_dir.resolve()}")
    print(f"  source:       {'embedded' if embed else repr(rel_path)}")
    print(f"  slot:         {slot}")
    print(f"  name:         {slot_name}")
    print(f"  rows updated: {rows_updated}/{len(new_data_lines)-1}")


@app.command()
def main(
    dwar_file: Path = typer.Argument(..., help="Path to the .dwar file", exists=True, file_okay=True, dir_okay=False),
    id_column: str = typer.Argument(..., help="Column whose values map to image filenames (e.g. Name)"),
    image_column: str = typer.Argument(..., help="Column to attach images to (can be same as id-column)"),
    image_dir: Optional[Path] = typer.Argument(None, help="Directory containing images (default: 'images/' next to .dwar file)"),
    embed: bool = typer.Option(False, '--embed', help="Embed images into the .dwar file instead of referencing them by path."),
    name: Optional[str] = typer.Option(None, '--name', help="Label for the image slot in DataWarrior (default: image-column name)."),
):
    """
    Attach images to a DataWarrior .dwar file.

    Images are matched by searching for <id-value>.png/.jpg/.jpeg (case-insensitive)
    in the image directory. The original file is backed up with a .bak extension.
    """
    add_images(dwar_file, id_column, image_column, image_dir, embed=embed, name=name)


if __name__ == '__main__':
    app()
