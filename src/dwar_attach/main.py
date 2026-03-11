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


def parse_dwar(content):
    """Parse a .dwar file into sections (fileinfo, column_props, data, hitlist, properties)."""
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
    m_prop = re.search(r'(<datawarrior properties>.*?</datawarrior properties>)', after_colprops, re.DOTALL)

    first_marker_pos = len(after_colprops)
    if m_hit:
        first_marker_pos = min(first_marker_pos, m_hit.start())
    if m_prop:
        first_marker_pos = min(first_marker_pos, m_prop.start())

    sections['data'] = after_colprops[:first_marker_pos]
    sections['hitlist'] = m_hit.group(1) if m_hit else None
    sections['properties'] = m_prop.group(1) if m_prop else None

    last_end = 0
    if m_prop:
        last_end = content.index(m_prop.group(1)) + len(m_prop.group(1))
    elif m_hit:
        last_end = content.index(m_hit.group(1)) + len(m_hit.group(1))
    sections['trailing'] = content[last_end:] if last_end else ''

    return sections


def detect_line_ending(content):
    return '\r\n' if '\r\n' in content else '\n'


def build_column_props_block(image_column, rel_path, mime_type):
    lines = [
        '<column properties>',
        f'<columnName="{image_column}">',
        '<columnProperty="detailCount\t1">',
        f'<columnProperty="detailSource0\trelPath:{rel_path}">',
        f'<columnProperty="detailType0\t{mime_type}">',
        f'<columnProperty="detailName0\t{image_column}">',
        '</column properties>',
    ]
    return '\n'.join(lines)


def update_column_props(existing, image_column, rel_path, mime_type):
    """Update or insert column properties for image_column in an existing block."""
    new_props = (
        f'<columnName="{image_column}">\n'
        f'<columnProperty="detailCount\t1">\n'
        f'<columnProperty="detailSource0\trelPath:{rel_path}">\n'
        f'<columnProperty="detailType0\t{mime_type}">\n'
        f'<columnProperty="detailName0\t{image_column}">\n'
    )
    if f'<columnName="{image_column}">' in existing:
        pattern = (
            rf'<columnName="{re.escape(image_column)}">.*?'
            rf'(?=<columnName=|</column properties>)'
        )
        return re.sub(pattern, new_props, existing, flags=re.DOTALL)
    else:
        return existing.replace('</column properties>', new_props + '</column properties>')


def compute_rel_path(dwar_path: Path, image_dir: Path):
    """Relative path from the .dwar file's directory to image_dir, with trailing slash.
    Returns empty string if they are the same directory."""
    dwar_dir = dwar_path.resolve().parent
    rel = image_dir.resolve().relative_to(dwar_dir, walk_up=True)
    if str(rel) == '.':
        return ''
    return str(rel).replace('\\', '/').rstrip('/') + '/'


def add_images(dwar_file: Path, id_column, image_column, image_dir: Optional[Path] = None):
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

    # Process rows
    missing = []
    mime_counts = Counter()
    new_data_lines = [data_lines[0]]

    for line in data_lines[1:]:
        cols = line.split('\t')
        while len(cols) <= max(id_idx, img_idx):
            cols.append('')

        id_val = cols[id_idx]
        filename, mime_type = find_image(image_dir, id_val)

        if filename:
            # Strip any existing |#| detail from the cell value, keep the display text
            display = cols[img_idx].split('|#|')[0] if '|#|' in cols[img_idx] else cols[img_idx]
            cols[img_idx] = display + f'|#|0:{filename}'
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

    if sections['column_props'] is None:
        new_col_props = build_column_props_block(image_column, rel_path, mime_type)
    else:
        new_col_props = update_column_props(sections['column_props'], image_column, rel_path, mime_type)

    parts = [sections['fileinfo'], linesep, new_col_props, new_data]
    if sections['hitlist']:
        parts.append(sections['hitlist'])
        parts.append(linesep)
    if sections['properties']:
        parts.append(sections['properties'])
        parts.append(linesep)

    new_content = ''.join(parts)

    backup = dwar_file.with_name(dwar_file.name + '.bak')
    shutil.copy2(dwar_file, backup)
    print(f"Backup: {backup}")

    with open(dwar_file, 'w', encoding='utf-8', newline='') as f:
        f.write(new_content)

    rows_updated = len(mime_counts.elements()) if False else sum(mime_counts.values())
    print(f"Updated: {dwar_file}")
    print(f"  id-column:    {id_column} (index {id_idx})")
    print(f"  image-column: {image_column} (index {img_idx})")
    print(f"  image-dir:    {image_dir.resolve()}")
    print(f"  relPath:      '{rel_path}'")
    print(f"  rows updated: {rows_updated}/{len(new_data_lines)-1}")


@app.command()
def main(
    dwar_file: Path = typer.Argument(..., help="Path to the .dwar file", exists=True, file_okay=True, dir_okay=False),
    id_column: str = typer.Argument(..., help="Column whose values map to image filenames (e.g. Name)"),
    image_column: str = typer.Argument(..., help="Column to attach images to (can be same as id-column)"),
    image_dir: Optional[Path] = typer.Argument(None, help="Directory containing images (default: 'images/' next to .dwar file)"),
):
    """
    Attach images to a DataWarrior .dwar file.

    Images are matched by searching for <id-value>.png/.jpg/.jpeg (case-insensitive)
    in the image directory. The original file is backed up with a .bak extension.
    """
    add_images(dwar_file, id_column, image_column, image_dir)


if __name__ == '__main__':
    app()
