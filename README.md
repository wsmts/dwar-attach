# dwar-attach

Attach images to columns in [DataWarrior](https://openmolecules.org/datawarrior/) `.dwar` files.

DataWarrior can display images in table and form views, but provides no GUI to configure this. `dwar-attach` automates the process by updating the column properties and cell references in the `.dwar` file.

## Usage

```bash
dwar-attach <dwar-file> <id-column> <image-column> [image-dir]
```

| Argument | Description |
|---|---|
| `dwar-file` | Path to the `.dwar` file |
| `id-column` | Column whose values map to image filenames (e.g. `Name`) |
| `image-column` | Column to attach images to (can be the same as `id-column`) |
| `image-dir` | Directory containing images (default: `images/` next to the `.dwar` file) |

Images are matched by searching for `<id-value>.png`, `.jpg`, or `.jpeg` (case-insensitive). The original file is backed up as `<file>.bak` before modification.

## Example

```
my-compounds/
├── compounds.dwar
└── images/
    ├── CIM123456.png
    ├── CIM123457.jpg
    └── ...
```

```bash
dwar-attach compounds.dwar Name Name
```

## Installation

Requires [uv](https://docs.astral.sh/uv/).

**Run without installing:**
```bash
uvx --from git+https://github.com/wsmts/dwar-attach dwar-attach compounds.dwar Name Name
```

**Install permanently:**
```bash
uv tool install git+https://github.com/wsmts/dwar-attach
```
