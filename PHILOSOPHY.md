You are working on a project called FORGER.

Your task is to evolve Forger into:

> A Vite-like development/build tool for Python combined with PyInstaller-like application bundling, but compiler-first, AST-aware, dependency-graph-driven, and fully extensible through Python plugins.

Do NOT treat this as merely a Python bundler.

Forger should become the Python equivalent of the developer experience Vite provides to JavaScript applications, while also providing deep application bundling capabilities similar to PyInstaller.

The architecture must be designed around a unified dependency graph.

============================================================
1. PRODUCT VISION
============================================================

Forger should eventually provide:

    forger dev
    forger build
    forger preview

with a workflow analogous to modern JavaScript tooling.

The core responsibilities are:

    Development
        - fast module discovery
        - incremental rebuilds
        - dependency graph
        - plugin hooks
        - future HMR support

    Compilation
        - Python parsing
        - AST analysis
        - scope analysis
        - symbol/reference analysis
        - AST transformations
        - tree shaking
        - optional name minification

    Bundling
        - Python modules
        - Python package dependencies
        - non-Python resources
        - dynamically discovered resources
        - plugin-provided resources
        - virtual modules/resources
        - runtime dependencies

    Output
        - immutable dist snapshot
        - deterministic output
        - preserved project-relative filesystem paths
        - content hashes
        - manifest

The fundamental architecture is:

    source
       ↓
    resolve
       ↓
    load
       ↓
    parse
       ↓
    plugin transforms
       ↓
    analyze
       ↓
    unified dependency graph
       ↓
    reachability analysis
       ↓
    AST tree shaking
       ↓
    resource pruning
       ↓
    optional name minification
       ↓
    final transforms
       ↓
    validation
       ↓
    immutable snapshot
       ↓
    dist/

============================================================
2. IMPORTANT: INSPECT BEFORE IMPLEMENTING
============================================================

Before changing code:

1. Inspect the entire repository.
2. Understand the existing architecture.
3. Identify:
   - parser
   - AST representation
   - module resolver
   - module graph
   - compiler
   - bundler
   - output writer
   - configuration system
   - CLI
   - plugin system, if any
   - tests
4. Determine which existing abstractions can be reused.
5. Do NOT create parallel implementations of functionality that already exists.
6. Do NOT rewrite the project blindly.
7. Produce an implementation plan based on the actual repository before making major architectural changes.

If the repository already has a dependency graph, evolve it instead of replacing it unnecessarily.

If there is already a plugin mechanism, extend it instead of creating a second one.

============================================================
3. UNIFIED DEPENDENCY GRAPH
============================================================

The dependency graph is the central abstraction of Forger.

Everything must participate in the same graph.

The graph must be able to represent at minimum:

    Python modules
    Python symbols
    Python imports
    Python symbol references
    resources/files
    virtual modules
    virtual resources
    generated modules
    generated resources

Conceptually:

    Module
       ├── defines → Symbol
       ├── imports → Module
       └── references → Resource

    Symbol
       ├── references → Symbol
       └── references → Resource

    VirtualModule
       └── behaves like Module

    Resource
       └── contains metadata/content/hash

Do NOT implement separate disconnected dependency systems for:

    Python
    assets
    plugins
    resources

Everything should ultimately become graph nodes and edges.

The graph should support:

    add_node()
    add_edge()
    remove_edge()
    mark_root()
    retain()
    reachable()
    dependencies()
    dependents()

Use stable internal node IDs where appropriate.

============================================================
4. AST-LEVEL TREE SHAKING
============================================================

Implement real AST/symbol-level tree shaking.

The goal is:

> Only Python code reachable from configured roots should survive.

Example:

    def used():
        return helper()

    def helper():
        return 42

    def unused():
        return 999

If only used() is reachable:

    used()
    helper()

survive.

unused() must be removed from the emitted AST.

Tree shaking MUST be scope-aware.

Use symbol/reference analysis rather than textual identifier matching.

Correctly handle:

    module scope
    function scope
    nested functions
    closures
    classes
    lambdas
    comprehensions
    global
    nonlocal
    decorators
    defaults
    annotations
    generators
    async functions
    exception handlers
    match statements

Preserve required side effects.

Do not remove code merely because its return value is unused if executing the code can have observable side effects.

============================================================
5. RESOURCE-AWARE TREE SHAKING
============================================================

The dependency graph is not Python-only.

Reachable Python code may reference files such as:

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

These resources must become graph nodes.

For example:

    def load_users():
        return open("data/users.csv")

must create:

    load_users
        ↓
    data/users.csv

If load_users is dead:

    load_users
    data/users.csv

should both disappear unless another reachable node references the CSV.

Do NOT simply copy every file from the project.

The output must be:

    snapshot(reachable_graph)

not:

    copy(source_directory)

============================================================
6. RESOURCE RESOLUTION
============================================================

Provide extensible static resource resolution.

Initially support common patterns such as:

    open("foo.csv")

    Path("foo.csv")

    Path("foo.csv").read_text()

    Path("foo.csv").read_bytes()

    Path(__file__).parent / "foo.csv"

and equivalent statically resolvable expressions.

Use constant propagation where the existing compiler can support it.

If a resource cannot be statically resolved, represent the uncertainty explicitly.

Do not guess.

For dynamic accesses such as:

    open(user_supplied_path)

support a conservative policy so the generated bundle does not silently become incorrect.

============================================================
7. RESOURCE RESOLVERS
============================================================

Resource discovery must be extensible.

Create a plugin API conceptually equivalent to:

    ResourceResolver

Plugins should be able to recognize framework-specific resource usage.

Examples:

    Django templates
    Jinja templates
    static assets
    Pandas files
    custom application loaders

These should become ordinary graph edges.

A plugin discovering:

    templates/index.html

must add a graph dependency.

It must NOT simply tell the output writer "copy this file."

============================================================
8. PRESERVE FILESYSTEM PATHS
============================================================

This is a HARD REQUIREMENT.

For every reachable file:

    source/<relative-path>
        ↓
    dist/<same-relative-path>

The project-relative path MUST remain unchanged.

For example:

    project/
        src/
            app/
                main.py
                data/
                    users.csv
            templates/
                index.html

becomes:

    dist/
        src/
            app/
                main.py
                data/
                    users.csv
            templates/
                index.html

if those files are reachable.

Do NOT:

    flatten directories
    rename files
    relocate resources
    create arbitrary asset directories
    move Python modules

Tree shaking determines WHETHER something survives.

It does NOT determine WHERE it lives.

The filesystem topology of surviving content must remain unchanged.

Absolute source filesystem paths must never become logical snapshot paths.

The canonical resource identity should be the normalized project-relative path.

============================================================
9. __file__ AND RELATIVE PATH SEMANTICS
============================================================

Preserve Python filesystem semantics.

For:

    Path(__file__).parent / "data" / "users.csv"

if the source relationship is:

    src/app/main.py
    src/app/data/users.csv

the snapshot must preserve:

    dist/src/app/main.py
    dist/src/app/data/users.csv

Do not unnecessarily rewrite such paths.

The goal is for relative filesystem semantics to remain valid inside dist/.

============================================================
10. IMMUTABLE SNAPSHOT
============================================================

forger build must produce an immutable application snapshot in:

    dist/

The snapshot must contain only reachable content.

The source tree may contain thousands of files.

Only files reachable through the dependency graph should appear in dist/.

The snapshot must be independently usable.

After generation:

    modifying source
    deleting source
    moving source

must not affect the already-generated dist/.

Generate snapshots atomically.

Do NOT partially overwrite a working dist/.

Use a temporary output directory and atomically replace the destination only after a successful build.

If compilation fails:

    existing dist/

must remain untouched.

============================================================
11. DETERMINISTIC OUTPUT
============================================================

Given identical:

    source
    configuration
    plugin versions/configuration

the output must be deterministic.

Ensure deterministic:

    graph traversal
    symbol ordering
    file ordering
    manifest ordering
    generated names
    generated metadata

Do not rely on filesystem traversal order.

============================================================
12. MANIFEST
============================================================

Generate:

    dist/manifest.json

The manifest should describe the snapshot.

Each emitted file should have:

    project-relative path
    type
    size
    content hash

Example:

    {
        "format_version": 1,
        "files": [
            {
                "path": "src/app/main.py",
                "type": "python",
                "size": 1234,
                "hash": "..."
            },
            {
                "path": "src/app/data/users.csv",
                "type": "resource",
                "size": 5678,
                "hash": "..."
            }
        ]
    }

Use a cryptographic hash available from Python's standard library.

============================================================
13. PYTHON PLUGIN SYSTEM
============================================================

Forger MUST have a first-class Python plugin architecture.

The plugin system should be inspired by:

    Vite
    Rollup

but designed for Python.

The fundamental philosophy is:

> If Forger does not understand something, a Python plugin should be able to teach it.

Plugins should be able to participate in every major pipeline stage.

Conceptually support:

    config
    config_resolved
    build_start

    resolve
    load

    parse

    transform_ast
    transform

    analyze
    discover_resources

    build_graph
    before_shake
    after_shake

    transform_resource

    generate
    write_bundle

    build_end

Do not necessarily expose every hook as a separate method if a cleaner API exists.

The API should be coherent and composable.

============================================================
14. PYTHONIC PLUGIN API
============================================================

A plugin should look conceptually like:

    class MyPlugin:
        name = "my-plugin"

        def resolve(self, specifier, importer, context):
            ...

        def load(self, id, context):
            ...

        def transform_ast(self, module, context):
            ...

        def analyze(self, module, context):
            ...

        def discover_resources(self, module, context):
            ...

        def transform_resource(self, resource, context):
            ...

        def build_end(self, context):
            ...

Use type hints.

Provide a stable Context API.

Avoid exposing arbitrary mutable internal compiler state.

============================================================
15. PLUGIN CONTEXT
============================================================

Provide a context object with controlled APIs such as:

    context.project_root
    context.config
    context.graph
    context.logger

    context.resolve(...)
    context.load(...)

    context.add_dependency(...)
    context.add_resource_dependency(...)

    context.retain_symbol(...)
    context.retain_module(...)
    context.retain_resource(...)

    context.virtual_module(...)
    context.virtual_resource(...)

Plugins should use these APIs rather than mutating private compiler structures.

============================================================
16. VIRTUAL MODULES
============================================================

Plugins must be able to create virtual modules.

Examples:

    virtual:config
    virtual:env
    virtual:generated-router

A virtual module should be able to:

    resolve
    load
    parse
    analyze
    participate in graph
    tree shake
    transform
    emit

It should behave like a normal module from the compiler's perspective.

============================================================
17. VIRTUAL RESOURCES
============================================================

Likewise support virtual resources.

Example:

    virtual:generated-schema.json

They must be able to participate in:

    graph
    reachability
    hashing
    manifest
    snapshot generation

============================================================
18. PLUGIN ORDERING
============================================================

Plugin ordering must be deterministic.

Support concepts such as:

    pre
    normal
    post

and explicit ordering/dependencies where necessary.

Do not rely on accidental Python import order.

============================================================
19. NAME MINIFICATION
============================================================

Implement Python identifier/name minification as a completely separate compiler pass.

It MUST be explicitly enabled in:

    forger.config.py

Default:

    minify_names = False

Example:

    # forger.config.py

    minify_names = True

The compiler MUST NEVER silently enable name minification.

Do not make:

    production mode
    forger build
    optimization mode
    plugin loading

automatically enable it.

============================================================
20. NAME MINIFICATION ORDER
============================================================

Name minification happens AFTER tree shaking.

The pipeline is:

    parse
       ↓
    analyze
       ↓
    reachability
       ↓
    tree shake
       ↓
    post-shake plugin transforms
       ↓
    name minification
       ↓
    validation
       ↓
    emit

There is no reason to minify dead symbols.

============================================================
21. NAME MINIFICATION SEMANTICS
============================================================

Use scope-aware symbol information.

Eligible identifiers include:

    local variables
    function parameters
    nested function locals
    internal functions
    internal classes
    private/internal symbols

Be conservative with:

    public APIs
    externally visible symbols
    attribute names
    dynamically accessed names
    framework-defined names

Do NOT blindly rename every AST Name node.

The minifier must operate on resolved symbols.

============================================================
22. DYNAMIC PYTHON
============================================================

Python is dynamic.

Handle carefully:

    getattr()
    setattr()
    globals()
    locals()
    vars()
    eval()
    exec()
    __import__()

Never blindly rewrite strings.

For example:

    getattr(obj, "foo")

does not automatically mean the string "foo" may be changed because a method called foo was renamed.

When the compiler cannot prove safety:

    preserve the name.

Correctness is more important than maximum compression.

============================================================
23. MINIFIER CONFIGURATION
============================================================

Support configuration conceptually like:

    minify_names = {
        "enabled": True,
        "keep": {
            "myapp.api.create_user",
            "myapp.api.delete_user",
        },
    }

The exact configuration structure can follow the existing configuration architecture.

Plugins must also be able to protect names.

============================================================
24. NAME GENERATION
============================================================

Generate deterministic valid Python identifiers:

    a
    b
    c
    ...
    z
    aa
    ab
    ...

Never generate Python keywords.

Avoid collisions with:

    existing identifiers
    builtins
    protected names
    generated names

Reuse short names between independent lexical scopes where safe.

============================================================
25. MODULE AND FILE NAMES MUST NOT BE MINIFIED
============================================================

Name minification only affects Python identifiers.

It must NOT rename:

    files
    directories
    Python module paths
    resource paths
    manifest paths

For example:

    src/myapp/services/users.py

must remain:

    dist/src/myapp/services/users.py

regardless of name minification.

============================================================
26. PLUGIN + MINIFICATION INTERACTION
============================================================

Plugins must be able to protect names.

Conceptually:

    context.keep_name(symbol)
    context.prevent_name_minification(symbol)

Framework plugins should be able to mark dynamically referenced names as protected.

Plugin transformations that introduce identifiers must occur before name minification unless they explicitly operate after minification.

============================================================
27. APPLICATION BUNDLING
============================================================

Forger should provide PyInstaller-like application bundling.

The goal is not simply:

    zip source code

The goal is:

    understand application graph
    ↓
    determine reachable application
    ↓
    include required Python modules
    ↓
    include required resources
    ↓
    include runtime dependencies
    ↓
    produce deployable snapshot

The bundler should eventually support:

    standalone directory
    archive/package output
    executable launcher

But do NOT attempt to implement every packaging target immediately.

Build a strong directory snapshot architecture first.

============================================================
28. RUNTIME DEPENDENCIES
============================================================

The architecture must leave room for native/runtime dependencies.

Eventually the graph may contain:

    Python modules
    resources
    shared libraries
    native extensions
    executables
    runtime metadata

Do not hardcode the graph to only Python source files.

============================================================
29. DEVELOPMENT MODE
============================================================

forger dev should be architected as a persistent compiler process.

It should maintain:

    module graph
    parsed ASTs
    dependency information
    plugin state
    caches

and rebuild only affected portions when files change.

Do NOT build dev mode as:

    spawn "forger build"
    every time a file changes

The architecture should support incremental compilation.

Future HMR support should be possible without redesigning the graph.

============================================================
30. BUILD MODE
============================================================

forger build should:

    resolve
    load
    parse
    analyze
    build graph
    discover resources
    determine reachability
    tree shake
    run configured transformations
    optionally minify names
    validate
    create atomic immutable dist snapshot

============================================================
31. CONFIGURATION
============================================================

The primary project configuration file is:

    forger.config.py

It should be capable of configuring:

    entry points
    plugins
    aliases
    resource roots
    tree-shaking behavior
    minification
    output
    runtime behavior

Example:

    from forger import define_config
    from my_plugin import MyPlugin

    config = define_config(
        entry=["src/main.py"],

        plugins=[
            MyPlugin(),
        ],

        minify_names=False,
    )

Keep configuration explicit and type-safe.

============================================================
32. ENTRY POINTS
============================================================

The compiler needs explicit application roots.

For example:

    entry=["src/main.py"]

Roots may also come from:

    configuration
    plugins
    framework integrations
    explicit retention APIs

The graph traversal starts from these roots.

============================================================
33. SIDE EFFECTS
============================================================

Tree shaking must account for Python's side effects.

Do not assume that an unused function/class definition is always equivalent to dead code if decorators, metaclasses, class bodies, module-level execution, or other observable behavior are involved.

The analysis must distinguish:

    declaration
    executable side effect

Be conservative when semantic certainty is unavailable.

============================================================
34. ERROR HANDLING
============================================================

Diagnostics should identify:

    source file
    line/column
    compiler phase
    plugin
    hook
    graph node

For example:

    PluginError
      Plugin: django
      Hook: discover_resources
      File: src/app/views.py
      Error: ...

Do not produce opaque errors.

============================================================
35. TESTING
============================================================

Build comprehensive tests.

At minimum test:

    module resolution
    Python imports
    symbol reachability
    dead functions
    dead classes
    nested scopes
    closures
    global
    nonlocal
    decorators
    side effects
    resource references
    dead resources
    Path(__file__)
    multiple references to one resource
    missing resources
    dynamic resources
    plugins
    virtual modules
    virtual resources
    plugin ordering
    plugin graph modifications
    tree-shaking plugin roots
    name minification
    protected names
    dynamic Python
    path preservation
    deterministic builds
    manifest
    atomic dist generation
    failed builds
    incremental rebuilds

============================================================
36. IMPORTANT TEST CASE
============================================================

Create an integration project resembling:

    project/
    ├── forger.config.py
    └── src/
        ├── main.py
        ├── app/
        │   ├── __init__.py
        │   ├── used.py
        │   ├── unused.py
        │   ├── data/
        │   │   ├── used.csv
        │   │   └── unused.csv
        │   └── templates/
        │       ├── used.html
        │       └── unused.html
        └── config/
            └── settings.json

main.py reaches:

    app.used

app.used reaches:

    data/used.csv
    templates/used.html

Nothing reaches:

    app/unused.py
    data/unused.csv
    templates/unused.html

The resulting dist must preserve:

    src/app/used.py
    src/app/data/used.csv
    src/app/templates/used.html

and must NOT contain:

    src/app/unused.py
    src/app/data/unused.csv
    src/app/templates/unused.html

If name minification is enabled, Python identifiers may change.

Filesystem paths MUST NOT.

============================================================
37. ARCHITECTURAL INVARIANTS
============================================================

These are hard requirements.

INVARIANT 1:

    The dependency graph is the source of truth.

INVARIANT 2:

    Tree shaking removes unreachable program nodes.

INVARIANT 3:

    Resource pruning removes unreachable resources.

INVARIANT 4:

    Unreachable code cannot keep a resource alive.

INVARIANT 5:

    Reachable files preserve their project-relative paths.

INVARIANT 6:

    Name minification is opt-in through forger.config.py.

INVARIANT 7:

    Name minification happens after tree shaking.

INVARIANT 8:

    Plugins operate through the unified graph.

INVARIANT 9:

    Plugins cannot silently bypass graph semantics.

INVARIANT 10:

    dist/ is an independent immutable snapshot.

INVARIANT 11:

    Failed builds never corrupt a previous valid dist/.

INVARIANT 12:

    Identical inputs produce deterministic output.

INVARIANT 13:

    The architecture must support incremental compilation.

INVARIANT 14:

    The architecture must be extensible to native/runtime dependencies.

============================================================
38. IMPLEMENTATION STRATEGY
============================================================

Do NOT attempt to build every future feature at once.

First inspect the repository and determine the current implementation state.

Then implement in logical phases:

PHASE 1
    establish/strengthen unified dependency graph

PHASE 2
    module + symbol resolution

PHASE 3
    AST analysis and reachability

PHASE 4
    AST tree shaking

PHASE 5
    resource discovery and graph integration

PHASE 6
    path-preserving immutable dist snapshot

PHASE 7
    Python plugin architecture

PHASE 8
    virtual modules/resources

PHASE 9
    optional Python name minification

PHASE 10
    incremental build infrastructure

PHASE 11
    development server/watch mode

PHASE 12
    future standalone/native bundling

Do not implement a later phase by creating architecture that contradicts earlier phases.

============================================================
39. CODE QUALITY
============================================================

Use:

    type hints
    dataclasses where appropriate
    protocols/interfaces where appropriate
    explicit result types
    structured diagnostics
    unit tests
    integration tests

Keep public APIs clean.

Avoid giant god classes.

Separate:

    graph
    resolver
    loader
    parser
    analyzer
    shaker
    minifier
    plugin manager
    snapshot writer
    configuration

but make them cooperate through well-defined interfaces.

============================================================
40. FINAL GOAL
============================================================

When this work is complete, Forger should conceptually be:

    Vite
       +
    Python compiler
       +
    AST tree shaker
       +
    resource-aware dependency graph
       +
    PyInstaller-style application bundler
       +
    Python plugin ecosystem
       +
    optional Python identifier minifier

The developer experience should be:

    write Python application
        ↓
    create forger.config.py
        ↓
    forger dev
        ↓
    fast development workflow

and:

    forger build
        ↓
    analyze entire application
        ↓
    eliminate unreachable code/resources
        ↓
    apply configured plugins
        ↓
    optionally minify Python names
        ↓
    produce deterministic immutable dist/
        ↓
    deploy dist/

The central philosophy is:

> FORGER FORGES A DEPLOYABLE PYTHON APPLICATION FROM ITS REACHABLE PROGRAM GRAPH.

Do not lose this architectural principle while implementing individual features.

Before making large changes, inspect the repository, explain the existing architecture, identify the minimum changes required, and then implement incrementally with tests after each major phase.