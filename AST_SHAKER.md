# Implement AST Tree Shaking and Immutable Application Graph Snapshot

We are building a high-performance Python compiler/bundler.

The bundler must perform **AST-level tree shaking** and then produce an **immutable snapshot of the entire reachable application graph** inside `dist/`.

This is NOT merely Python module bundling.

The dependency graph must include:

* Python modules
* Python symbols
* referenced files
* referenced data assets
* templates
* static files
* configuration files
* other explicitly supported resources

The fundamental goal is:

> Starting from application entry points, determine everything reachable through Python AST references and dependencies, then emit only the reachable program and assets into an immutable `dist/` snapshot.

---

# 1. Overall Pipeline

The compiler pipeline should conceptually become:

```text
Source Project
      │
      ▼
Module Resolution
      │
      ▼
Python AST
      │
      ▼
Symbol / Scope Analysis
      │
      ▼
AST Reference Analysis
      │
      ├───────────────┐
      ▼               ▼
Python Symbols     Resource References
      │               │
      └───────┬───────┘
              ▼
       Unified Dependency Graph
              │
              ▼
       Reachability Analysis
              │
              ▼
       Reachable Graph Snapshot
              │
       ┌──────┴──────┐
       ▼             ▼
 AST Tree Shake   Asset Collection
       │             │
       └──────┬──────┘
              ▼
             dist/
```

The dependency graph is the central source of truth.

---

# 2. Unified Graph

Do NOT create a Python-only graph and a completely separate asset system.

Model all dependencies in one graph abstraction.

Conceptually:

```text
Node
 ├── PYTHON_MODULE
 ├── PYTHON_SYMBOL
 ├── FILE
 ├── TEMPLATE
 ├── STATIC_ASSET
 └── RESOURCE
```

Edges represent relationships:

```text
PythonModule
    └── defines → PythonSymbol

PythonSymbol
    └── references → PythonSymbol

PythonSymbol
    └── references → File

PythonModule
    └── imports → PythonModule

File
    └── references → File
```

The implementation does not have to use these exact classes, but the architecture must support this model.

Prefer compact integer node IDs internally.

---

# 3. AST-Level Tree Shaking

Retain the previously specified symbol-level tree shaking behavior.

Starting from roots:

```text
entry points
exports
framework roots
explicitly retained symbols
```

perform symbol reachability analysis.

For:

```python
# utils.py

def used():
    return helper()

def helper():
    return 42

def unused():
    return 999
```

and:

```python
from utils import used

used()
```

retain:

```text
utils.used
utils.helper
```

and remove:

```text
utils.unused
```

The AST transformation must happen only AFTER reachability analysis is complete.

---

# 4. Resource / File References

This is a first-class requirement.

The tree shaker must detect **filesystem resources referenced from reachable Python code**.

Examples:

```python
open("data/users.csv")
```

```python
Path("data/users.csv").read_text()
```

```python
Path(__file__).parent / "data" / "users.csv"
```

```python
with open("config.json") as f:
    ...
```

```python
pd.read_csv("dataset.csv")
```

The generic analyzer should not hardcode every third-party API.

Instead provide a resource-reference mechanism.

Conceptually:

```text
reachable Python AST
        │
        ▼
resource reference analyzer
        │
        ▼
"data/users.csv"
        │
        ▼
resource node
```

---

# 5. Static Path Resolution

Resolve resource paths whenever they can be determined statically.

Examples:

```python
open("foo.csv")
```

→ known resource:

```text
foo.csv
```

---

```python
name = "foo.csv"
open(name)
```

If constant propagation can prove `name == "foo.csv"`, resolve it.

---

```python
base = Path(__file__).parent
path = base / "data" / "foo.csv"
open(path)
```

If the compiler can statically resolve this expression, resolve it.

Do NOT require full constant propagation in the first implementation.

Instead expose an extensible resolver:

```text
ResourceResolver
```

that can return:

```text
RESOLVED
UNKNOWN
DYNAMIC
```

---

# 6. Resource References Must Be Scope-Aware

Do not perform naïve string matching.

For:

```python
foo = "something.csv"
```

the existence of `.csv` in a string does NOT automatically mean it is a resource.

Only classify it as a resource when it participates in a recognized resource access pattern or an explicit resource declaration.

For example:

```python
open("data/users.csv")
```

is a resource reference.

But:

```python
message = "users.csv was processed"
```

is not.

---

# 7. Resource Resolver Plugins

Create an extensible API such as:

```text
ResourceResolver
    can_resolve(call/expression)
    resolve(call/expression, context)
```

Built-in resolvers should initially understand common standard-library patterns:

```text
open()
pathlib.Path
Path.read_text()
Path.read_bytes()
```

Architecture should allow future resolvers for:

```text
Django
Jinja
Pandas
SQLAlchemy
custom framework APIs
```

without modifying the core tree shaker.

---

# 8. Explicit Resource API

Provide a way for compiler integrations to explicitly declare resources.

Conceptually:

```text
retain_resource("templates/index.html")
retain_resource("static/app.css")
```

These become graph roots.

This is important for frameworks where resources are discovered indirectly.

---

# 9. Dynamic Resource Access

For:

```python
open(user_supplied_path)
```

the compiler cannot know which file is required.

Do NOT guess.

The analysis should mark the access as:

```text
DYNAMIC_RESOURCE_ACCESS
```

and apply the configured safety policy.

Possible policies:

```text
strict
conservative
explicit
```

In conservative mode, retain the appropriate resource scope rather than silently producing a broken bundle.

---

# 10. Path Traversal Safety

Every resolved resource must be normalized against the project root.

Reject or properly handle:

```text
../../secret
```

and absolute paths outside the project.

A resource should only enter the snapshot if it belongs to an allowed source/resource root.

Do not allow bundling to accidentally escape the project boundary.

---

# 11. Resource Deduplication

If multiple symbols reference:

```text
data/users.csv
```

the graph must contain exactly one logical resource node.

Example:

```text
foo.py ──┐
         ├──> data/users.csv
bar.py ──┘
```

The file should only be copied once.

---

# 12. Resource Dependency Propagation

Resources participate in reachability exactly like Python symbols.

Example:

```text
main.py
   │
   ▼
app.py
   │
   ├──> templates/index.html
   │
   └──> data/users.csv
```

If `app.py` is reachable:

```text
app.py
templates/index.html
data/users.csv
```

are reachable.

If the symbol containing the reference to `users.csv` is eliminated:

```text
data/users.csv
```

should also become unreachable unless another reachable node references it.

This is critical.

Resource reachability must originate from **reachable AST/symbol references**, not merely from scanning the entire source tree.

---

# 13. Immutable Snapshot

After reachability analysis, generate:

```text
dist/
```

as an **immutable snapshot of the reachable application graph**.

`dist/` must contain only reachable content.

For example:

```text
project/
├── src/
│   ├── main.py
│   ├── app.py
│   ├── unused.py
│   └── data/
│       ├── users.csv
│       └── unused.csv
│
└── dist/
    ├── main.py
    ├── app.py
    └── data/
        └── users.csv
```

The following must NOT appear:

```text
unused.py
unused.csv
```

unless explicitly retained.

---

# 14. Snapshot Semantics

The generated `dist/` directory represents a specific compiler input state.

It must not depend on the source tree after generation.

Everything required at runtime must exist inside `dist/`.

The resulting directory should therefore be usable as a standalone deployment artifact.

Conceptually:

```text
source tree
     │
     │ compile
     ▼
immutable snapshot
     │
     ▼
dist/
```

After snapshot creation, deleting or modifying the source tree should not affect the contents of the snapshot.

---

# 15. Atomic Snapshot Generation

Do not partially mutate an existing `dist/` directory.

Generate into a temporary directory:

```text
dist.tmp.<id>/
```

Then atomically replace:

```text
dist/
```

with the completed snapshot.

The exact mechanism should use the safest atomic filesystem operation available on the target platform.

If snapshot generation fails:

```text
existing dist/
```

must remain untouched.

---

# 16. Deterministic Output

Given identical source inputs and compiler configuration:

```text
source A
    +
configuration A
```

must produce the same logical snapshot.

Ensure deterministic:

* file ordering
* module ordering
* graph ordering
* metadata ordering
* manifest ordering

Do not rely on filesystem traversal order.

---

# 17. Snapshot Manifest

Generate a machine-readable manifest inside `dist/`.

For example:

```text
dist/
├── ...
└── manifest.json
```

The manifest should describe the snapshot.

Conceptually:

```json
{
  "format_version": 1,
  "entry_points": [
    "main"
  ],
  "files": [
    {
      "path": "main.py",
      "type": "python",
      "hash": "...",
      "size": 1234
    },
    {
      "path": "data/users.csv",
      "type": "resource",
      "hash": "...",
      "size": 9876
    }
  ]
}
```

Do not hardcode this exact schema if the existing architecture has a better metadata format.

The manifest should allow a deployment system to determine exactly what the snapshot contains.

---

# 18. Content Integrity

Every emitted file should have a content hash.

Prefer a cryptographic hash already available in the standard library.

The hash should be computed from the final emitted bytes.

This allows:

```text
source graph
      ↓
snapshot
      ↓
content hashes
```

to provide deterministic integrity information.

---

# 19. Immutable Means No Source References

Do NOT emit:

```python
open("../src/data/foo.csv")
```

or paths that depend on the source checkout.

Resource references in emitted Python must resolve against the snapshot.

If the compiler relocates:

```text
src/data/foo.csv
```

to:

```text
dist/data/foo.csv
```

the runtime path semantics must remain correct.

This is particularly important for:

```python
__file__
Path(__file__)
```

and relative resource access.

---

# 20. `__file__` Semantics

Preserve correct runtime semantics for:

```python
Path(__file__).parent / "data.csv"
```

If the source module is relocated inside `dist`, the generated module must still resolve the resource correctly.

Do not blindly rewrite paths if preserving normal Python `__file__` semantics already solves the problem.

---

# 21. Python AST Transformation

When emitting a Python module:

1. Analyze original AST.
2. Determine reachable symbols.
3. Remove unreachable definitions.
4. Preserve reachable statements.
5. Preserve required imports.
6. Preserve required side effects.
7. Preserve resource references.
8. Emit the transformed AST.
9. Compile/unparse using the existing compiler pipeline.

Never mutate the source AST during the analysis phase.

---

# 22. Graph Snapshot vs Source Snapshot

The snapshot is NOT:

```text
copy everything from src/
```

It is:

```text
snapshot(reachable_graph)
```

This distinction is fundamental.

If the project contains:

```text
10,000 files
```

but only:

```text
742 files/resources
```

are reachable, the snapshot should contain only those 742.

---

# 23. Dead Resource Elimination

This must work transitively.

Example:

```python
def used():
    return open("used.csv")

def unused():
    return open("unused.csv")
```

If only `used()` is reachable:

```text
keep:
    used()
    used.csv

remove:
    unused()
    unused.csv
```

This should be enforced by the graph.

Do NOT simply copy every `.csv` found under the project directory.

---

# 24. Resource Types

The initial architecture should support arbitrary files:

```text
.csv
.json
.yaml
.toml
.txt
.html
.css
.js
.png
.jpg
.webp
.svg
.bin
```

Do not make `.csv` a special hardcoded category.

The dependency graph should represent:

```text
RESOURCE
```

with a path and metadata.

File extension can be metadata only.

---

# 25. Framework Hooks

Framework integrations should be able to add graph edges.

For example:

```text
Django analyzer
    │
    ├── template references
    ├── static resources
    ├── settings references
    └── framework registration
```

These should enter the same unified reachability analysis.

Do not create a separate "Django copy everything" mechanism.

---

# 26. Final Architecture

The desired architecture should look approximately like:

```text
                    ┌──────────────────┐
                    │   Entry Points   │
                    └────────┬─────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Reference Analyzer │
                  └──────────┬──────────┘
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
        Python Symbol Edges        Resource Edges
                │                         │
                └────────────┬────────────┘
                             ▼
                  ┌─────────────────────┐
                  │  Unified Graph      │
                  └──────────┬──────────┘
                             ▼
                  ┌─────────────────────┐
                  │ Reachability Pass   │
                  └──────────┬──────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
      Reachable AST Nodes            Reachable Resources
              │                             │
              ▼                             ▼
       AST Tree Shaker                Asset Collector
              │                             │
              └──────────────┬──────────────┘
                             ▼
                    ┌────────────────┐
                    │ Snapshot Writer│
                    └───────┬────────┘
                            ▼
                           dist/
```

---

# 27. Critical Invariants

The implementation must maintain these invariants:

### Invariant 1

Every emitted Python definition is reachable from a root or explicitly retained.

### Invariant 2

Every emitted resource is reachable from a reachable graph node or explicitly retained.

### Invariant 3

An unreachable Python symbol must not keep its referenced resources alive.

### Invariant 4

An unreachable module must not keep its resources alive.

### Invariant 5

The generated `dist/` contains everything required to execute the reachable program under the supported runtime model.

### Invariant 6

`dist/` is an independent snapshot and must not depend on the source checkout.

### Invariant 7

A failed build must never destroy a previously valid snapshot.

### Invariant 8

The output must be deterministic for identical inputs.

---

# 28. Testing

Add integration tests that verify the complete graph.

Example:

```text
project/
├── main.py
├── app.py
├── unused.py
├── data/
│   ├── used.csv
│   └── unused.csv
└── templates/
    ├── used.html
    └── unused.html
```

Where:

```python
# app.py

def used():
    with open("data/used.csv"):
        ...

def unused():
    with open("data/unused.csv"):
        ...
```

If only `used()` is reachable:

```text
dist/
├── main.py
├── app.py
├── data/
│   └── used.csv
└── manifest.json
```

`unused.py`, `unused.csv`, and `unused.html` must not appear.

Also test:

* multiple references to the same resource
* resources referenced by multiple modules
* resource references inside nested functions
* resources inside dead functions
* dynamically constructed paths
* `Path(__file__)`
* decorators
* default arguments
* class bodies
* package-relative resources
* resource files outside the Python package
* symlinks
* path traversal
* missing resources
* resource hash changes
* deterministic builds
* failed snapshot generation
* atomic replacement
* empty snapshots
* multiple entry points

---

# 29. Performance

This compiler is intended to be extremely fast.

Do not repeatedly:

```text
walk AST
scan filesystem
resolve same path
hash same file
rebuild symbol table
```

Cache aggressively where correctness permits.

The ideal pipeline is:

```text
parse once
    ↓
index once
    ↓
analyze references
    ↓
reachability
    ↓
transform
    ↓
write snapshot
```

Resource metadata and content hashes should also be cached where possible.

Avoid unnecessary copying.

---

# 30. Implementation Instructions

Before writing code:

1. Inspect the existing compiler architecture.
2. Find the current module graph.
3. Find the AST representation.
4. Find symbol/scope analysis.
5. Find the compiler's file/resource handling.
6. Find the existing `dist`/output mechanism.
7. Reuse existing abstractions wherever possible.

Do not create parallel infrastructure unnecessarily.

Implement:

```text
SymbolReachabilityAnalyzer
ASTPruner
ResourceReferenceAnalyzer
UnifiedDependencyGraph
SnapshotWriter
```

or equivalent components that fit the existing architecture.

The most important architectural principle is:

> **The compiler should produce `dist/` from the reachable dependency graph, not from the source directory.**

The final output is therefore an **immutable, deterministic, self-contained snapshot of the reachable application graph**.
