# Path Preservation Requirement

The generated `dist/` snapshot MUST preserve the exact **project-relative filesystem path** of every reachable file.

Tree shaking determines **whether a file or AST node survives**.

It must NOT determine where the surviving file is placed.

## Fundamental Rule

For every reachable source/resource file:

```text
source/<project-relative-path>
        ↓
dist/<same-project-relative-path>
```

The relative path must remain identical.

For example:

```text
project/
├── src/
│   ├── app/
│   │   ├── main.py
│   │   └── data/
│   │       └── users.csv
│   ├── templates/
│   │   └── index.html
│   └── config/
│       └── settings.json
```

The snapshot MUST be:

```text
dist/
├── src/
│   ├── app/
│   │   ├── main.py
│   │   └── data/
│   │       └── users.csv
│   ├── templates/
│   │   └── index.html
│   └── config/
│       └── settings.json
└── manifest.json
```

If `templates/index.html` is unreachable, it is removed entirely:

```text
dist/
├── src/
│   ├── app/
│   │   ├── main.py
│   │   └── data/
│   │       └── users.csv
│   └── config/
│       └── settings.json
└── manifest.json
```

But if it survives, its path remains:

```text
src/templates/index.html
```

It must never become:

```text
templates/index.html
index.html
assets/index.html
```

## Path Identity

The graph should identify every filesystem resource by its normalized **project-relative path**.

Conceptually:

```text
ResourceNode
    project_relative_path
    source_absolute_path
    resource_type
    content_hash
```

The canonical identity should be the project-relative path.

For example:

```text
src/app/data/users.csv
```

is the graph identity.

Do not use the absolute source path as the logical identity.

## No Relocation

The snapshot writer MUST NOT:

* flatten directories
* move assets
* rename files
* rename directories
* group resources by type
* place Python modules in a separate directory
* place static files in a generated asset directory
* rewrite relative paths merely because a file was bundled

The snapshot is a **pruned mirror of the source tree**.

The only difference is that unreachable content is absent and reachable Python ASTs may have been transformed.

## Python Module Paths

Python module paths must also remain compatible with their original filesystem layout.

For example:

```text
src/project/
├── __init__.py
├── settings.py
└── app/
    ├── __init__.py
    └── views.py
```

must remain:

```text
dist/src/project/
├── __init__.py
├── settings.py
└── app/
    ├── __init__.py
    └── views.py
```

Do not flatten:

```text
dist/project/
```

or:

```text
dist/settings.py
dist/views.py
```

unless the compiler's explicitly configured source-root semantics already define such a transformation.

## Relative Resource References

Because paths are preserved, relative resource access should remain semantically valid whenever the runtime environment is also rooted at the snapshot.

For example:

```python
from pathlib import Path

DATA = Path(__file__).parent / "data" / "users.csv"
```

If the source is:

```text
src/app/main.py
src/app/data/users.csv
```

the snapshot must preserve:

```text
dist/src/app/main.py
dist/src/app/data/users.csv
```

so the relative relationship remains unchanged.

Do NOT rewrite:

```python
Path(__file__).parent / "data" / "users.csv"
```

into some generated path.

## Absolute Paths

Absolute source filesystem paths must never be embedded into the snapshot as resource identities.

For example, do not emit:

```text
/home/user/project/src/app/data/users.csv
```

as the logical resource path.

Use:

```text
src/app/data/users.csv
```

instead.

## Manifest

The manifest must record the preserved project-relative path.

Example:

```json
{
  "path": "src/app/data/users.csv",
  "type": "resource",
  "hash": "...",
  "size": 1234
}
```

Never record a generated/relocated destination path when the destination is identical to the project-relative path.

## Source Root

If the compiler supports a configurable source root, define path preservation relative to that configured project root explicitly.

Do not accidentally preserve paths relative to the current working directory.

The compiler must have one canonical:

```text
PROJECT_ROOT
```

and every snapshot path must be:

```text
relative_to(PROJECT_ROOT)
```

## Hard Invariant

The snapshot operation is:

```text
dist_path = project_relative_path
```

not:

```text
dist_path = generated_bundle_path
```

Therefore:

> **Tree shaking removes unreachable content; it never changes the filesystem topology of reachable content.**

The resulting `dist/` directory is a **pruned, transformed mirror of the original project tree**.
