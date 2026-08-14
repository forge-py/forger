# Vite-Style Python Plugin System

The compiler/bundler MUST expose a first-class **Python plugin API** that allows users to customize and extend every major stage of the compilation and bundling pipeline.

The plugin system should be conceptually similar to Vite/Rollup plugins, but designed specifically for a Python compiler/bundler.

Plugins are Python code.

The core compiler must remain independent of framework-specific behavior.

---

# 1. Design Goal

A user should be able to write something conceptually like:

```python
class MyPlugin:
    name = "my-plugin"

    def resolve(self, specifier, importer, context):
        ...

    def load(self, id, context):
        ...

    def transform(self, module, context):
        ...

    def analyze(self, module, context):
        ...

    def add_resources(self, module, context):
        ...

    def finalize(self, graph, context):
        ...
```

and register it:

```python
plugins = [
    MyPlugin(),
]
```

The exact API can differ based on the existing architecture.

The important requirement is that plugins can participate in **every meaningful compiler stage**.

---

# 2. Pipeline

The plugin lifecycle should correspond to the compiler pipeline:

```text
                    Compiler
                       │
                       ▼
                 Plugin Setup
                       │
                       ▼
                Configuration
                       │
                       ▼
                  Resolution
                       │
                       ▼
                    Loading
                       │
                       ▼
                  Parsing / AST
                       │
                       ▼
               Symbol Analysis
                       │
                       ▼
              Reference Analysis
                       │
                       ▼
              Resource Discovery
                       │
                       ▼
              Reachability / Shake
                       │
                       ▼
               AST Transformation
                       │
                       ▼
               Resource Transform
                       │
                       ▼
                Snapshot Writer
                       │
                       ▼
                  Finalization
```

Plugins must be able to hook into these stages.

---

# 3. Plugin Lifecycle

Support lifecycle hooks equivalent to:

```text
config
config_resolved

build_start

resolve
load

parse

analyze

transform

discover_resources

build_graph

before_shake
after_shake

generate

write_bundle

build_end
```

Do not necessarily expose every hook as a separate method if that creates unnecessary complexity.

The API should be clean and composable.

---

# 4. Resolution Hook

Plugins must be able to participate in import/module/resource resolution.

Conceptually:

```python
def resolve(self, specifier, importer, context):
    ...
```

Example:

```python
class VirtualModulePlugin:
    name = "virtual"

    def resolve(self, specifier, importer, context):
        if specifier == "virtual:config":
            return context.virtual_id("config")
```

The plugin may return:

```text
RESOLVED
UNRESOLVED
```

or an equivalent result type.

Resolution must support:

* Python imports
* resource paths
* virtual modules
* aliases
* custom protocols
* generated modules

---

# 5. Loading Hook

Plugins must be able to provide module/resource contents.

Conceptually:

```python
def load(self, id, context):
    ...
```

For example:

```python
class EnvPlugin:
    name = "env"

    def load(self, id, context):
        if id == "virtual:env":
            return "VALUE = ..."
```

The loaded content should be able to become:

* Python source
* AST
* resource bytes
* generated content

The core pipeline should understand the returned type.

---

# 6. AST Hooks

Plugins must be able to inspect and modify Python ASTs.

Provide hooks conceptually like:

```python
def transform_ast(self, module, context):
    ...
```

A plugin may:

* add nodes
* remove nodes
* replace nodes
* rewrite imports
* rewrite expressions
* inject definitions
* annotate nodes
* add metadata

AST transformations must happen before the relevant downstream analysis if the transformation affects reachability.

The plugin system must clearly define hook ordering.

---

# 7. Transform Ordering

Multiple plugins may transform the same module.

For:

```text
plugin A
plugin B
plugin C
```

the result should be deterministic:

```text
AST
 ↓
A
 ↓
B
 ↓
C
 ↓
compiler analysis
```

Allow plugins to specify ordering constraints.

Conceptually support:

```python
enforce = "pre"
```

and:

```python
enforce = "post"
```

or an equivalent mechanism.

Do not rely on accidental Python import ordering.

---

# 8. Analysis Hooks

Plugins must be able to participate in semantic analysis.

Conceptually:

```python
def analyze(self, module, context):
    ...
```

A plugin may tell the compiler:

```text
this symbol is reachable
this symbol is dynamic
this resource is required
this module must be retained
this reference exists
```

For example:

```python
context.retain_symbol("myapp.handlers.handle_request")
```

or:

```python
context.retain_resource("templates/index.html")
```

The exact API should use internal graph IDs rather than strings wherever possible.

---

# 9. Graph API

Plugins must have controlled access to the unified dependency graph.

Provide operations conceptually like:

```python
context.graph.add_node(...)
context.graph.add_edge(...)
context.graph.mark_root(...)
context.graph.retain(...)
```

Plugins should be able to create:

```text
Python module
Python symbol
resource
virtual module
virtual resource
```

and dependency edges.

Example:

```python
def analyze(self, module, context):
    context.add_resource_dependency(
        module,
        "templates/index.html",
    )
```

This resource then participates normally in reachability analysis.

The plugin must NOT need to implement its own reachability system.

---

# 10. Resource Discovery Hooks

Plugins must be able to teach the compiler about framework-specific resource references.

Example:

```python
class DjangoTemplatePlugin:
    name = "django-template"

    def discover_resources(self, module, context):
        ...
```

If it discovers:

```text
templates/home.html
```

it adds the resource to the same unified graph.

This is how framework integrations should work.

Do NOT hardcode Django, Pandas, Jinja, etc. into the core compiler.

---

# 11. Tree-Shaking Hooks

Plugins must be able to influence tree shaking.

Provide APIs such as:

```python
context.retain_symbol(symbol)
context.retain_module(module)
context.retain_resource(resource)
context.mark_dynamic(symbol)
```

Plugins may also inspect:

```python
context.is_reachable(symbol)
```

after the analysis phase.

Allow a plugin to declare conservative retention when static analysis cannot determine usage.

Example:

```python
class DjangoPlugin:
    def after_analyze(self, context):
        for model in discovered_models:
            context.retain_symbol(model)
```

---

# 12. Post-Shake Transformation

Plugins must be able to modify the already-shaken AST.

This is important because a plugin may want to:

* inject runtime support
* rewrite imports
* optimize generated code
* add metadata
* generate wrappers

Provide a post-shake AST hook distinct from the normal transformation hook.

Conceptually:

```text
load
 ↓
parse
 ↓
pre-analysis transform
 ↓
analysis
 ↓
tree shaking
 ↓
post-shake transform
 ↓
emit
```

---

# 13. Resource Transformation

Plugins must also be able to transform resources.

For example:

```python
def transform_resource(self, resource, context):
    ...
```

Potential uses:

```text
CSV → compact binary
JSON → optimized JSON
template → compiled template
SVG → optimized SVG
```

The plugin must be able to change:

* content
* metadata
* output representation

while preserving the resource's logical project-relative path unless it explicitly requests a new output path.

---

# 14. Virtual Modules

Support virtual modules.

For example:

```text
virtual:config
virtual:generated-router
virtual:env
```

A plugin should be able to:

```text
resolve virtual module
        ↓
generate source/AST
        ↓
compile normally
        ↓
participate in graph
        ↓
tree shake normally
```

Virtual modules must integrate with the same dependency graph.

---

# 15. Virtual Resources

Likewise support virtual resources.

Example:

```text
virtual:generated-schema.json
```

They should be capable of becoming reachable graph nodes and being emitted into `dist/` if appropriate.

---

# 16. Plugin Context

Provide a stable context object rather than passing dozens of arguments.

Conceptually:

```python
context.project_root
context.config
context.graph
context.logger
context.compiler
context.resolve(...)
context.load(...)
context.retain_symbol(...)
context.retain_module(...)
context.retain_resource(...)
context.add_dependency(...)
context.emit(...)
```

The exact API should be designed carefully.

Do not expose mutable internal compiler state indiscriminately.

---

# 17. Plugin Isolation

A plugin should not be able to accidentally corrupt the compiler's internal invariants.

Prefer controlled APIs.

For example, instead of:

```python
context.graph._nodes.append(...)
```

provide:

```python
context.add_node(...)
```

The graph remains responsible for maintaining:

* node identity
* edge consistency
* path normalization
* reachability state
* symbol identity

---

# 18. Plugin Configuration

Support project-level configuration.

Conceptually:

```python
# compiler.config.py

plugins = [
    MyPlugin(),
    DjangoPlugin(),
]
```

Plugins should optionally expose configuration:

```python
MyPlugin(
    option=True,
    mode="production",
)
```

Avoid requiring plugins to modify global compiler state.

---

# 19. Plugin Ordering

Plugin execution order must be deterministic.

Support explicit ordering constraints.

Conceptually:

```python
class Plugin:
    name = "my-plugin"
    enforce = "normal"
    order = 100
```

Or an equivalent mechanism.

Support:

```text
pre
normal
post
```

and explicit dependencies where necessary.

---

# 20. Plugin Errors

Plugin failures must be reported with useful context:

```text
Plugin: django-template
Hook: discover_resources
Module: src/app/views.py
Error: ...
```

Do not expose an opaque stack trace as the only diagnostic.

The compiler should preserve the distinction between:

```text
compiler error
plugin error
user source error
resource error
```

---

# 21. Caching

The plugin architecture must eventually support incremental builds.

Plugins should expose enough information for cache invalidation.

A plugin should be able to declare:

```text
inputs
outputs
dependencies
```

Conceptually:

```python
plugin.cache_key(context)
```

or an equivalent mechanism.

Do not implement a complex incremental compiler unless the architecture requires it, but do not design the plugin API in a way that makes incremental compilation impossible later.

---

# 22. Determinism

Plugin execution must not destroy deterministic builds.

For identical:

```text
source
configuration
plugin versions
plugin configuration
```

the resulting graph and `dist/` snapshot should be identical.

Where plugins generate content, their outputs become part of the snapshot's inputs.

---

# 23. Plugin Hooks and Graph Snapshot

The final lifecycle should approximately be:

```text
PLUGIN SETUP
     │
     ▼
CONFIG
     │
     ▼
RESOLVE
     │
     ▼
LOAD
     │
     ▼
PARSE
     │
     ▼
PRE-ANALYSIS TRANSFORMS
     │
     ▼
SYMBOL / SCOPE ANALYSIS
     │
     ▼
REFERENCE ANALYSIS
     │
     ▼
RESOURCE DISCOVERY
     │
     ▼
GRAPH CONSTRUCTION
     │
     ▼
PLUGIN GRAPH HOOKS
     │
     ▼
REACHABILITY ANALYSIS
     │
     ▼
AST TREE SHAKING
     │
     ▼
POST-SHAKE TRANSFORMS
     │
     ▼
RESOURCE TRANSFORMS
     │
     ▼
SNAPSHOT GENERATION
     │
     ▼
OUTPUT HOOKS
     │
     ▼
BUILD END
```

Every stage should have a well-defined plugin extension point.

---

# 24. Plugin API Should Be Pythonic

The plugin API should feel natural to Python developers.

Prefer:

```python
class MyPlugin:
    name = "my-plugin"

    def resolve(self, specifier, importer, context):
        ...

    def transform_ast(self, module, context):
        ...

    def discover_resources(self, module, context):
        ...
```

over a huge configuration dictionary.

Use type hints throughout the public API.

Provide clear protocol/base-class definitions.

---

# 25. No Third-Party Dependencies in Core

The compiler core must remain dependency-free unless an existing project requirement explicitly says otherwise.

The plugin system itself must not require a third-party plugin framework.

Use Python's standard library for:

* plugin registration
* protocols
* dataclasses
* import mechanisms
* errors
* logging

If a plugin wants third-party dependencies, that should be the plugin's responsibility.

---

# 26. CLI

Provide a mechanism for loading plugins from the project configuration.

For example:

```text
compiler build
```

automatically loads:

```text
compiler.config.py
```

and its plugins.

If the project architecture uses another configuration filename, integrate with the existing configuration system.

The CLI should also support debugging plugin execution, for example:

```text
compiler build --debug-plugins
```

which can show:

```text
[plugin] django-template: discover_resources
[plugin] my-plugin: transform_ast
[plugin] my-plugin: retain_resource
```

without making verbose logging the default.

---

# 27. Plugin Testing API

Provide utilities for plugin authors to test against an isolated compiler context.

A plugin test should be able to construct:

```text
source
+
plugin
+
configuration
```

and inspect:

```text
graph
reachable symbols
reachable resources
transformed AST
dist/
```

without needing to invoke the entire CLI process.

---

# 28. Important Architectural Principle

The plugin system must NOT bypass the compiler's core graph.

For example, a plugin discovering:

```text
templates/index.html
```

must add:

```text
PythonSymbol
      │
      └── references → Resource
```

to the unified graph.

It must NOT simply tell the snapshot writer:

```text
"also copy templates/index.html"
```

unless the resource is intentionally declared as an explicit root.

This ensures that:

* tree shaking
* resource pruning
* dependency tracking
* hashing
* manifest generation
* snapshot generation

all operate on the same dependency model.

---

# 29. Final Architectural Model

The compiler should ultimately look like:

```text
                         ┌──────────────┐
                         │    Plugins   │
                         └──────┬───────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
              ▼                 ▼                  ▼
          Resolution          AST             Resources
              │                 │                  │
              └─────────────────┼──────────────────┘
                                ▼
                     Unified Dependency Graph
                                │
                                ▼
                       Reachability Analysis
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
          AST Tree Shaking                Resource Pruning
                │                               │
                └───────────────┬───────────────┘
                                ▼
                       Snapshot Transformation
                                │
                                ▼
                              dist/
```

The plugin system should be treated as a **core compiler architecture feature**, not an afterthought.

The desired developer experience is:

> "If the compiler doesn't understand something, write a Python plugin that teaches it."

A plugin should be able to introduce new resolution rules, loaders, AST transformations, resource discovery, graph edges, tree-shaking roots, resource transformations, virtual modules, and output behavior — while still using the compiler's unified dependency graph and immutable `dist/` snapshot machinery.
