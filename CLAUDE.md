# Forger — Development Instructions

## Mission

Build **Forger**, a Python application compiler, analyzer, bundler, and cross-platform executable builder.

Forger should provide a workflow conceptually similar to Go:

```text
Python project
      │
      ▼
forger compile
      │
      ▼
   app.forge
      │
      ├──────────────┬──────────────┬──────────────┐
      ▼              ▼              ▼              ▼
 Windows           Linux         Android          WASM
   .exe              ELF           APK             wasm
```

The goal is to turn a Python application into a self-contained executable/application while preserving **actual CPython behavior and quirks**.

Forger is NOT:

* a new Python interpreter
* a Python-to-C compiler
* a replacement for CPython
* a framework-specific packager
* a giant collection of hardcoded framework rules
* a reason to rewrite CPython

Forger uses real CPython as its execution runtime.

---

# Absolute Design Principles

## 1. Correctness before optimization

Python is extremely dynamic.

If Forger cannot prove something is unnecessary:

> KEEP IT.

Never remove something merely because a simplistic static analysis failed to find it.

The optimization hierarchy is:

```text
PROVEN REQUIRED
        ↓
      KEEP

PROVEN UNNECESSARY
        ↓
      REMOVE

UNKNOWN
        ↓
 KEEP + DIAGNOSTIC
```

---

## 2. Core performance is critical

The Forger core must be **EXTREMELY FAST**.

Assume Forger will eventually process:

* huge Python applications
* large virtual environments
* thousands of packages
* millions of files
* large dependency graphs
* many parallel builds

Do not build the core around slow Python-level filesystem walking, parsing, graph construction, or repeated subprocess execution.

Use **Rust for performance-critical infrastructure**.

Rust should handle, where appropriate:

```text
filesystem traversal
file discovery
hashing
dependency graph
module resolution
path normalization
VFS construction
archive generation
parallel processing
cache management
incremental analysis
bytecode handling
native dependency discovery
serialization
build orchestration
```

Python should be used where Python semantics or framework-specific knowledge are required.

---

# 3. Python is the optimizer layer

Framework and ecosystem intelligence belongs in Python.

Do NOT hardcode framework-specific logic into Rust.

The architecture should resemble:

```text
                     Forger
                       │
              ┌────────┴────────┐
              ▼                 ▼
             Rust             Python
             Core             Optimizers
              │                 │
              │       ┌─────────┼─────────┐
              │       ▼         ▼         ▼
              │    django.py  flask.py  ...
              │
              └────────┬────────┘
                       ▼
                Dependency Graph
                       │
                       ▼
                    .forge
```

Rust provides the high-performance infrastructure.

Python provides semantic intelligence.

---

# 4. No third-party runtime dependencies

The Forger core must not depend on arbitrary third-party runtime libraries.

Prefer:

```text
Rust standard library
Python standard library
CPython APIs
```

where practical.

If functionality genuinely requires an external implementation:

1. First determine whether it can be implemented efficiently using the standard library.
2. If not, investigate whether the required functionality can be borrowed/adapted from high-quality projects.
3. Prefer proven implementations from the **Astral/Ruff ecosystem** where technically appropriate.
4. Avoid pulling in large dependency trees.
5. Do not add dependencies merely for convenience.

Potentially useful Ruff/Astral codebases may be studied or reused where licensing and architecture permit.

Examples include concepts from:

```text
ruff
uv
red-knot / pyrefly-adjacent tooling where appropriate
```

Do not blindly copy code.

Verify licenses and compatibility before incorporating any implementation.

---

# Development Toolchain

Use **uv** for Python project management.

The project must be runnable using:

```bash
uv sync
```

Tests must use:

```bash
uv run pytest
```

Static type checking should use:

```bash
uv run pyrefly check
```

Do not introduce Poetry, Pipenv, PDM, or another Python package manager.

`uv` is the canonical Python development workflow.

---

# Testing

Use **pytest**.

Tests must cover:

```text
unit tests
integration tests
end-to-end compilation tests
cross-platform build tests where available
CPython compatibility tests
dependency-resolution tests
VFS tests
optimizer tests
dynamic-import tests
resource-discovery tests
native-extension tests
reproducibility tests
incremental-build tests
```

The most important test pattern is:

```text
normal CPython execution
        VS
Forger-built execution
```

Their observable behavior should match.

---

# Type Checking

Use **Pyrefly** for Python static analysis.

Python code should be typed.

Avoid:

```python
Any
```

unless genuinely necessary.

Prefer explicit protocols/interfaces for communication between the Rust core and Python optimizer layer.

Run:

```bash
uv run pyrefly check
```

regularly during development.

---

# Architecture

The project should be divided into several major components.

```text
forger/
├── core/                 # Rust
│
├── python/               # Python integration layer
│
├── optimizers/           # Python ecosystem/framework analyzers
│
├── cli/                  # CLI integration
│
├── runtime/              # runtime/bootstrap infrastructure
│
├── build/                # target build logic
│
└── tests/
```

The exact repository layout may evolve.

The architectural separation must not.

---

# Rust Core

The Rust core owns performance-critical operations.

It should provide APIs for:

```text
file discovery
path resolution
module resolution
dependency graph manipulation
parallel analysis
hashing
caching
VFS
artifact serialization
artifact deserialization
native dependency inspection
build graph
incremental rebuild detection
```

Rust must be aggressively parallel where operations are independent.

Avoid unnecessary allocations.

Avoid repeated path parsing.

Avoid repeated filesystem syscalls.

Use efficient data structures.

Measure performance before optimizing.

---

# Python Layer

Python owns semantic analysis that benefits from executing or understanding Python.

Examples:

```text
AST analysis
Python import semantics
framework configuration analysis
framework optimizers
forger.py
Python-specific metadata
dynamic configuration interpretation
```

Python should communicate with Rust through a narrow, efficient interface.

Do not constantly cross the Rust/Python boundary for individual filesystem operations.

Batch operations.

Prefer:

```text
Rust:
    "Here is a batch of files / graph nodes / metadata."

Python:
    "Here are the dependencies I discovered."

Rust:
    merge them efficiently.
```

over:

```text
Python → Rust
Python → Rust
Python → Rust
Python → Rust
...
```

---

# `forger compile`

`compile` is the analysis and bundling stage.

```bash
forger compile
```

Conceptually:

```text
source
  │
  ├── Rust filesystem analysis
  ├── Python static analysis
  ├── package metadata
  ├── Python optimizer passes
  ├── optional runtime tracing
  └── forger.py
          │
          ▼
   unified dependency graph
          │
          ▼
        .forge
```

`compile` must NOT produce a platform-specific executable.

The result is a platform-independent application artifact.

---

# `.forge`

`.forge` is the intermediate application artifact.

It should contain enough information to build the application for different targets without re-analyzing the original source.

Conceptually:

```text
app.forge
├── manifest
├── modules
├── stdlib
├── native extensions
├── native libraries
├── resources
├── VFS metadata
├── dependency graph
└── build metadata
```

The format should be deterministic and versioned.

---

# `forger build`

`build` consumes `.forge` and links/packages it with a target CPython runtime.

Example:

```bash
forger build app.forge --target windows-x64
```

Conceptually:

```text
.forge
   +
target CPython
   +
native dependencies
   +
Forger bootstrap
   ↓
executable
```

`build` must not need to repeat application dependency analysis.

---

# CPython

Support CPython **3.10+**.

The exact supported versions should be determined by the runtime/build system, but the architecture must not assume a single Python version.

CPython remains the execution engine.

Do not implement Python semantics yourself.

Do not replace CPython with a custom VM.

Do not translate Python into C.

---

# CPython Standard Library Optimization

Forger must analyze CPython's standard library and bundle only what is required when safe.

Example:

```python
import json
```

should not automatically result in the entire CPython standard library being bundled.

However, dynamic behavior must be respected.

Analyze:

```text
stdlib imports
native stdlib extensions
importlib
sys.path
dynamic imports
package discovery
runtime resource access
```

Never aggressively remove standard-library components unless reachability is understood.

---

# Dependency Graph

The dependency graph is the central abstraction.

It must support nodes representing:

```text
Python modules
Python packages
bytecode
native extensions
native libraries
shared libraries
resources
templates
static files
configuration
schemas
fixtures
migrations
plugins
entry points
virtual filesystem paths
runtime dependencies
```

Edges should capture why a dependency exists.

Example:

```text
app.main
   │
   ├── IMPORT → app.models
   │
   ├── RESOURCE → templates/index.html
   │
   ├── NATIVE → _ssl
   │
   └── DYNAMIC_IMPORT → app.plugins.foo
```

Every dependency should ideally have provenance.

---

# Static Analysis

Static analysis should use Python's AST and related mechanisms.

Analyze:

```text
import
from ... import ...
relative imports
importlib
__import__
sys.path
sys.modules
pkgutil
filesystem access
importlib.resources
importlib.metadata
entry points
native loading
configuration
reflection
exec
eval
compile
```

Static analysis is not assumed to be complete.

---

# Runtime Analysis

Provide runtime tracing using actual CPython.

The tracer should observe generic behavior rather than framework-specific behavior.

Record events such as:

```text
IMPORT
DYNAMIC_IMPORT
SYSPATH_CHANGE
FILE_OPEN
FILE_READ
DIRECTORY_SCAN
RESOURCE_ACCESS
NATIVE_LIBRARY_LOAD
NATIVE_EXTENSION_LOAD
ENTRYPOINT_DISCOVERY
PROCESS_EXEC
```

Runtime tracing supplements static analysis.

It does not replace it.

---

# Framework Optimizers

Framework-specific intelligence MUST live outside the Rust core.

Optimizers are Python modules.

Example:

```text
optimizers/
├── django.py
├── flask.py
├── jinja2.py
├── sqlalchemy.py
├── celery.py
└── ...
```

These optimizers should actively analyze real projects.

They should not merely match filenames.

---

# Optimizer Detection

Optimizers should be discoverable.

For example:

```text
project
   │
   ▼
generic analysis
   │
   ▼
detect installed packages
   │
   ▼
available optimizer
   │
   ▼
run optimizer
```

The core should not contain:

```text
if framework == django
if framework == flask
if framework == ...
```

Instead, Python optimizers register themselves through a clean interface.

---

# Django Optimizer Example

The Django optimizer should actively inspect project configuration.

It should locate and analyze `settings.py`.

It should inspect things such as:

```text
INSTALLED_APPS
TEMPLATES
STATICFILES_DIRS
STATIC_ROOT
LOCALE_PATHS
FIXTURE_DIRS
MIDDLEWARE
ROOT_URLCONF
ASGI_APPLICATION
WSGI_APPLICATION
```

For example:

```python
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
    }
]
```

The optimizer should resolve:

```text
BASE_DIR / templates
```

and add the resulting resources to the dependency graph.

If:

```text
APP_DIRS = True
```

the optimizer should inspect reachable Django applications for:

```text
templates/
static/
locale/
migrations/
management/
templatetags/
fixtures/
```

The optimizer must actively analyze configuration rather than blindly assuming conventional directories.

---

# Flask/Jinja Optimizers

Flask/Jinja optimizers should analyze actual application configuration.

For example:

```python
Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
```

should allow the optimizer to resolve those directories.

Jinja loaders should be analyzed:

```text
FileSystemLoader
PackageLoader
ChoiceLoader
PrefixLoader
```

The optimizer should resolve loader search paths and contribute resources.

---

# General Optimizer API

Define a small, stable Python optimizer API.

Conceptually:

```python
class Optimizer:
    def detect(self, context):
        ...

    def analyze(self, context):
        ...

    def dependencies(self, context):
        ...
```

The exact API may differ.

The important requirement is:

> Optimizers contribute information to the same dependency graph used by generic analysis.

They must not create separate bundling systems.

---

# `forger.py`

Projects may contain:

```text
forger.py
```

This is the project's explicit build-time extension point.

It is NOT the runtime application.

It may specify dependencies that cannot be inferred automatically.

Example:

```python
from forger import include

include("templates/**/*")
include("generated/**/*")
```

It may also perform project-specific discovery.

Example:

```python
from forger import include_module

for plugin in discover_my_plugins():
    include_module(plugin)
```

`forger.py` feeds information into the same dependency graph.

---

# `forger.py` Safety

`forger.py` runs during the build/compile process.

It must never be bundled automatically merely because it exists.

The Forger API should make a clear distinction between:

```text
build-time code
runtime application code
bundled resources
```

---

# Resource Discovery

Resources are first-class dependencies.

Support generic mechanisms including:

```python
open(...)
Path.open(...)
Path.read_text(...)
Path.read_bytes(...)

glob.glob(...)
Path.glob(...)
Path.rglob(...)

os.listdir(...)
os.scandir(...)

importlib.resources
pkgutil.get_data(...)
```

Do not assume resources have specific file extensions.

A Python project may contain arbitrary data.

---

# Native Extensions

Support:

```text
.pyd
.so
.dylib
DLLs
shared libraries
```

Native dependencies must be target-specific.

A `.forge` artifact may contain multiple target variants.

Example:

```text
extension:
    module = foo.bar

    windows-x64 = ...
    linux-x64 = ...
    linux-arm64 = ...
    android-arm64 = ...
```

Build selects the compatible artifact.

---

# Virtual Filesystem

The `.forge` artifact contains a logical VFS.

The runtime should expose the VFS to Python while preserving important semantics such as:

```text
sys.path
__file__
__package__
__spec__
relative imports
absolute imports
importlib
importlib.resources
pkgutil
```

Do not assume everything must be extracted to a temporary directory.

Prefer direct VFS access where practical.

---

# Cross Platform

Architecture must support:

```text
Windows x64
Windows ARM64

Linux x64
Linux ARM64

macOS x64
macOS ARM64

Android ARM64

iOS ARM64

Emscripten / WASM
```

Targets may be implemented incrementally.

Do not allow the architecture to become desktop-only.

---

# Android

Use a CPython runtime built for Android.

Package:

```text
Forger runtime
CPython
.forge/VFS
native extensions
native libraries
```

into an Android-compatible application.

---

# iOS

Use a compatible CPython runtime built for iOS.

Build-time compilation/linking is performed before deployment.

Do not depend on dynamically compiling arbitrary native code on the device.

---

# Emscripten

Use an Emscripten-compatible CPython/runtime.

The VFS should provide the application with a consistent logical filesystem.

---

# Performance Requirements

Performance is a first-class feature.

The implementation should be benchmarked against realistic projects.

Measure:

```text
cold compile
warm compile
incremental compile
filesystem scanning
module resolution
dependency graph construction
optimizer execution
VFS generation
artifact serialization
build
```

Use parallelism aggressively where safe.

Cache aggressively.

Avoid repeated work.

Use content hashes for incremental builds.

Do not sacrifice correctness for benchmarks.

---

# No Premature Abstraction

Keep the core interfaces small.

Do not create elaborate plugin systems before a concrete use case requires them.

However, preserve the fundamental separation:

```text
Rust performance core
Python semantic analyzers
Python framework optimizers
project forger.py
target-specific builders
```

---

# Reproducibility

Given identical:

```text
source
dependencies
Python version
Forger version
target
configuration
```

Forger should produce deterministic `.forge` artifacts whenever possible.

Avoid embedding:

```text
timestamps
random identifiers
machine-specific absolute paths
```

unless explicitly required.

---

# Diagnostics

Diagnostics should explain:

```text
what was discovered
why it was included
why it was removed
why something is uncertain
which optimizer discovered it
which source caused the dependency
```

Example:

```text
Resource included:

templates/accounts/login.html

Reason:
Django optimizer

Source:
settings.py:TEMPLATES[0].DIRS

```

Or:

```text
Dependency retained conservatively:

myapp.plugins.*

Reason:
dynamic import could not be statically resolved

Recommendation:
use forger.py to explicitly declare plugin discovery
```

---

# Repository Rules

Before implementing anything:

1. Inspect the repository.
2. Understand existing architecture.
3. Do not blindly replace existing code.
4. Establish the Rust/Python boundary.
5. Establish `.forge` format.
6. Establish dependency graph representation.
7. Establish optimizer API.
8. Establish `forger.py` API.
9. Establish CLI.
10. Establish tests.

Implement incrementally.

Every major subsystem must have tests.

---

# Dependency Policy

Before adding ANY dependency:

1. Ask whether the standard library is sufficient.
2. Ask whether Rust standard facilities are sufficient.
3. Ask whether the functionality can be implemented directly.
4. Investigate equivalent implementations from Ruff/Astral.
5. Consider whether a small amount of borrowed/adapted code is preferable.
6. Check licensing.
7. Check maintenance implications.
8. Check compile-time and runtime cost.

Do not add a dependency simply because it is convenient.

The project should remain extremely lightweight.

---

# Important Anti-Patterns

DO NOT:

```text
hardcode Django behavior in Rust
hardcode Flask behavior in Rust
hardcode framework names into the dependency graph
write a Python interpreter
translate Python to C
reimplement CPython
walk the entire filesystem repeatedly
spawn Python subprocesses for every file
cross the Rust/Python boundary per filesystem operation
silently delete uncertain dependencies
bundle the entire .venv by default
assume imports are the entire dependency graph
assume templates are the only non-code resources
```

Instead:

```text
Rust → speed
Python → semantics
Optimizers → ecosystem knowledge
forger.py → project-specific knowledge
CPython → execution semantics
VFS → unified application filesystem
.forge → portable application artifact
```

---

# Definition of Done

A minimal successful implementation must eventually demonstrate:

```bash
uv sync

uv run pytest

uv run pyrefly check

forger compile

forger build --target windows-x64
```

and produce a standalone executable that:

* does not require the user's `.venv`
* does not require Python to be installed
* contains only the required application dependencies where safely determinable
* executes using real CPython
* preserves Python import semantics
* preserves important filesystem/resource semantics
* supports native extensions
* supports framework optimizers
* supports `forger.py`
* produces a `.forge` artifact that can be rebuilt for another target

The long-term goal is:

> **Forger should make Python applications deploy like Go applications without requiring Python developers to abandon Python's runtime semantics.**

The guiding architecture is:

```text
                         PYTHON PROJECT
                               │
                               ▼
                    ┌─────────────────────┐
                    │   forger compile    │
                    └──────────┬──────────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
             Rust core     Python analysis   forger.py
                │              │              │
                │       framework optimizers │
                │              │              │
                └──────────────┼──────────────┘
                               ▼
                       DEPENDENCY GRAPH
                               │
                               ▼
                           app.forge
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
           CPython/        CPython/        CPython/
           Windows          Android          WASM
                │              │              │
                ▼              ▼              ▼
              .exe             APK           .wasm
```

**Build the fast part in Rust. Build the intelligent part in Python. Keep the core dependency-free. Use actual CPython. Make framework knowledge modular. Make project-specific knowledge programmable through `forger.py`.**
