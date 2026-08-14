# Plan: Python Name Minification

Implement an optional **Python identifier/name minification pass** for the compiler/bundler.

This must be a **separate, independently executable compiler stage** from AST tree shaking.

The feature MUST be explicitly enabled in `forger.config.py`.

---

# 1. Explicit Configuration

Name minification MUST be opt-in.

The default behavior is:

```python
# forger.config.py

minify_names = False
```

or, preferably, an explicit configuration object:

```python
from forger import define_config

config = define_config(
    minify_names=False,
)
```

If the existing configuration architecture has a better pattern, use it.

The important invariant is:

> **Names must never be minified unless the user explicitly enables name minification in `forger.config.py`.**

A normal build must preserve original Python identifiers.

---

# 2. Explicit Minification Configuration

Support configuration such as:

```python
# forger.config.py

minify_names = True
```

Optionally support more granular configuration:

```python
minify = {
    "names": True,
}
```

or:

```python
minify_names = {
    "enabled": True,
}
```

Choose the configuration style that best fits the existing compiler architecture.

Do NOT introduce multiple competing configuration mechanisms.

---

# 3. Pipeline Position

Name minification MUST happen after:

```text
parse
  ↓
AST analysis
  ↓
symbol/reference analysis
  ↓
tree shaking
  ↓
AST pruning
  ↓
NAME MINIFICATION
  ↓
final AST
  ↓
emit
  ↓
dist/
```

The reason is important:

> Only minify symbols that survive tree shaking.

For example:

```python
def very_long_unused_function_name():
    ...

def very_long_used_function_name():
    return 42
```

After tree shaking:

```python
def very_long_used_function_name():
    return 42
```

Only then should the remaining identifier be considered for minification.

---

# 4. What Gets Minified

The initial implementation should support renaming:

* local variables
* function parameters
* nested function locals
* local function names
* local class names
* module-private symbols
* internal functions
* internal classes
* internal constants

Example:

```python
def calculate_total(long_parameter_name):
    intermediate_value = long_parameter_name * 2
    another_intermediate_value = intermediate_value + 10
    return another_intermediate_value
```

could become conceptually:

```python
def calculate_total(a):
    b = a * 2
    c = b + 10
    return c
```

The exact generated names should be determined by the minifier.

---

# 5. Do NOT Blindly Rename Everything

Python has many identifiers whose names have semantic significance.

Never blindly rename:

```text
__name__
__file__
__package__
__spec__
__loader__
__builtins__
```

or other interpreter-defined special names.

Likewise, do not rename Python syntax keywords.

Use the compiler's symbol table rather than textual identifier replacement.

---

# 6. Scope-Aware Renaming

Name minification MUST operate on resolved symbols.

Given:

```python
x = 1

def foo():
    x = 2
    return x
```

the two `x` identifiers are separate bindings.

The minifier must understand:

```text
module.x
foo.x
```

as different symbols.

Never implement this as:

```text
AST identifier string → replacement string
```

without scope information.

---

# 7. Lexical Scope

Correctly support:

* module scope
* function scope
* nested functions
* lambda scope
* comprehension scope
* class scope
* exception variables
* function parameters
* `global`
* `nonlocal`

Example:

```python
value = 10

def outer(value):
    def inner():
        return value

    return inner()
```

The renaming must preserve the closure relationship.

---

# 8. Globals

Be extremely careful with module-level names.

A module-level symbol may be accessed externally.

For example:

```python
# api.py

def public_function():
    ...

def internal_helper():
    ...
```

If another module contains:

```python
from api import public_function
```

the compiler may rename the internal representation consistently.

However, names that are part of the application's externally visible API must remain stable unless the user explicitly allows public API renaming.

---

# 9. Public vs Private Names

Introduce the concept of:

```text
PUBLIC
INTERNAL
```

symbols.

Initially, the conservative behavior should be:

```text
public API → preserve
internal symbols → eligible for minification
locals → eligible for minification
```

A future configuration can allow aggressive public-symbol renaming.

---

# 10. Explicit Keep Names

Provide a mechanism to preserve names:

```python
minify_names = {
    "enabled": True,
    "keep": {
        "myapp.api.public_function",
        "myapp.settings",
    },
}
```

Or expose the equivalent through the plugin/context API.

This is particularly important for framework integrations.

---

# 11. Plugin Integration

The name minifier must integrate with the plugin architecture.

Plugins should be able to:

```text
retain name
forbid rename
allow rename
provide externally referenced symbols
```

Conceptually:

```python
context.keep_name(symbol)
context.prevent_name_minification(symbol)
```

This allows framework plugins to protect names discovered dynamically.

---

# 12. Dynamic Python Features

Python's dynamic behavior makes aggressive renaming dangerous.

Special handling is required for:

```python
getattr(obj, "name")
setattr(obj, "name", value)

globals()["name"]
locals()["name"]

vars(obj)["name"]

eval(...)
exec(...)

__import__(...)
```

If the compiler cannot prove that a string corresponds to a safely renameable symbol, preserve the relevant name.

Do NOT assume:

```python
getattr(obj, "foo")
```

is equivalent to a statically resolved `foo` attribute.

---

# 13. String References

Never globally replace strings.

For example:

```python
name = "long_variable_name"
```

must NOT automatically become:

```python
name = "a"
```

unless the compiler has established that the string is semantically tied to the renamed symbol.

This distinction is critical.

---

# 14. Attribute Names

Do not automatically rename:

```python
obj.long_method_name()
```

just because the method is internally defined as:

```python
class Foo:
    def long_method_name(self):
        ...
```

Attribute names can form part of an external protocol.

The initial implementation should therefore be conservative:

```text
local variable names       → minify
function parameters       → minify
private/internal symbols   → minify
public attributes          → preserve
public methods             → preserve
```

Only introduce attribute-name minification as a separate explicitly enabled optimization later.

---

# 15. Name Generation

Use a deterministic name generator.

A typical sequence might be:

```text
a
b
c
d
...
z
aa
ab
ac
...
```

Names must be valid Python identifiers.

Never generate:

```text
1
2
a-b
```

or Python keywords.

Skip reserved names.

For example:

```text
and
as
assert
async
await
break
case
class
continue
def
del
elif
else
except
False
finally
for
from
global
if
import
in
is
lambda
match
None
nonlocal
not
or
pass
raise
return
True
try
type
while
with
yield
```

The exact reserved-name list should come from Python's standard facilities rather than being manually duplicated where possible.

---

# 16. Frequency-Aware Names

The minifier should eventually support frequency-aware allocation.

For example:

```python
def function(long_name):
    return long_name + other_long_name
```

A variable referenced frequently should preferably receive a shorter/common identifier.

However, do not sacrifice deterministic output.

The first implementation may simply allocate names in deterministic symbol order.

---

# 17. Scope-Aware Name Reuse

Names can be reused across independent scopes.

For example:

```python
def first():
    very_long_name = 1
    return very_long_name

def second():
    another_long_name = 2
    return another_long_name
```

can safely use:

```python
def first():
    a = 1
    return a

def second():
    a = 2
    return a
```

The minifier should exploit scope boundaries to minimize the total identifier size.

This is an important optimization.

---

# 18. Collision Prevention

The minifier MUST guarantee that generated names do not collide with:

* existing names in the same scope
* imported names
* builtins where shadowing would alter behavior
* names protected by plugins
* names required by dynamic access
* Python keywords
* compiler-generated names

Example:

```python
def foo(a):
    b = 1
```

must not rename another symbol to `a` if that creates a collision in the same scope.

---

# 19. Builtins

Be conservative around builtin names:

```text
open
print
len
str
int
list
dict
set
type
id
sum
map
filter
```

Do not rename a local variable to a builtin name if doing so changes the semantics of existing code.

Example:

```python
def foo():
    value = len(items)
```

Do not turn `value` into `len` if that would shadow `len` before its use.

The allocator must consider the complete lexical scope.

---

# 20. Imports

Imports must be handled through the symbol table.

For:

```python
from utils import very_long_function_name
```

if that imported binding is eligible for renaming, all local references must be updated consistently.

The implementation must distinguish:

```text
local binding
external symbol
module name
attribute name
```

Do not rename the external module's public API merely because the local binding is being minified.

For example:

```python
from utils import very_long_function_name as a
```

may be valid if only the local binding is renamed.

---

# 21. Module Names

Do NOT rename filesystem/module paths as part of this pass.

Given:

```text
src/my_application/utils.py
```

the file remains:

```text
dist/src/my_application/utils.py
```

Name minification affects identifiers in the AST.

It does NOT affect:

* project-relative file paths
* module filenames
* package directories
* resource paths
* manifest paths

Path preservation remains a hard invariant.

---

# 22. Interaction With Tree Shaking

Tree shaking MUST happen first.

Example:

```python
def extremely_long_unused_function_name():
    ...

def extremely_long_used_function_name():
    return helper_with_extremely_long_name()

def helper_with_extremely_long_name():
    return 42
```

After tree shaking:

```text
extremely_long_used_function_name
helper_with_extremely_long_name
```

remain.

Name minification then operates on those surviving symbols.

This prevents wasting analysis and rename work on dead code.

---

# 23. Interaction With Plugins

Final ordering should be clearly defined.

Recommended:

```text
resolve
 ↓
load
 ↓
parse
 ↓
plugin AST transforms
 ↓
symbol analysis
 ↓
resource discovery
 ↓
reachability
 ↓
tree shaking
 ↓
plugin post-shake transforms
 ↓
name minification
 ↓
final validation
 ↓
emit
```

If a plugin performs a transformation that introduces new identifiers, that transformation must happen BEFORE name minification.

Plugins that need to inspect final names should have a post-minification hook.

---

# 24. Validation Pass

After minification, run a validation pass.

Verify:

* every reference resolves
* every renamed symbol has exactly one binding
* no scope collisions exist
* imports remain valid
* globals remain valid
* nonlocals remain valid
* closures remain valid
* decorators remain valid
* class definitions remain valid
* generated AST compiles successfully

The minifier must fail safely rather than emitting potentially corrupted Python.

---

# 25. Configuration Examples

### Normal build

```python
# forger.config.py

minify_names = False
```

No names are changed.

### Production build

```python
# forger.config.py

minify_names = True
```

Enable name minification.

### Production with protected API

```python
# forger.config.py

minify_names = {
    "enabled": True,
    "keep": {
        "myapp.api.create_user",
        "myapp.api.delete_user",
    },
}
```

### Plugin-driven protection

Framework plugins should be able to mark dynamically discovered symbols as non-minifiable.

---

# 26. CLI Behavior

Do NOT make the CLI silently override configuration.

The source of truth is:

```text
forger.config.py
```

The CLI may provide inspection/debugging options such as:

```text
forger build --explain-minification
```

but the actual enable/disable decision must come from configuration.

---

# 27. Minification Report

When requested, provide a report such as:

```text
Name minification
────────────────────────────

Renamed:
  myapp.utils.calculate_total → a
  myapp.utils.intermediate_value → b
  myapp.worker.process → c

Preserved:
  myapp.api.create_user
  myapp.api.delete_user

Reason:
  public API
  plugin protected
  dynamic access
```

Do not generate this expensive report during normal builds.

---

# 28. Testing

Test:

* local variables
* function parameters
* nested functions
* closures
* `global`
* `nonlocal`
* comprehensions
* lambdas
* exception variables
* imports
* aliases
* classes
* methods
* decorators
* defaults
* annotations
* generators
* async functions
* pattern matching
* walrus operator
* `with`
* `for`
* `try`
* `match`

Also test dynamic constructs:

```python
getattr
setattr
globals
locals
vars
eval
exec
```

and verify conservative preservation.

---

# 29. Performance

Name minification should operate only on the **post-tree-shaking AST**.

Do not traverse dead AST nodes.

Use the existing symbol table and scope analysis.

Avoid rebuilding scope information.

Prefer:

```text
existing symbol table
        ↓
eligible symbols
        ↓
deterministic allocation
        ↓
single AST rewrite
```

The pass should be approximately linear in the number of surviving AST nodes/symbols.

---

# 30. Final Compiler Architecture

The resulting compiler pipeline should be:

```text
                    SOURCE
                       │
                       ▼
                  RESOLUTION
                       │
                       ▼
                    LOADING
                       │
                       ▼
                  PARSING / AST
                       │
                       ▼
               PLUGIN TRANSFORMS
                       │
                       ▼
               SYMBOL ANALYSIS
                       │
                       ▼
             REFERENCE ANALYSIS
                       │
                       ▼
             RESOURCE DISCOVERY
                       │
                       ▼
              UNIFIED GRAPH
                       │
                       ▼
             REACHABILITY ANALYSIS
                       │
                       ▼
                AST TREE SHAKE
                       │
                       ▼
              POST-SHAKE PLUGINS
                       │
                       ▼
            ┌──────────────────────┐
            │  NAME MINIFICATION   │
            │  IF CONFIGURED ONLY   │
            └──────────┬───────────┘
                       │
                       ▼
                 FINAL VALIDATION
                       │
                       ▼
                 AST / RESOURCES
                       │
                       ▼
               IMMUTABLE SNAPSHOT
                       │
                       ▼
                      dist/
```

The hard rule is:

> **Tree shaking decides what survives. Name minification decides how surviving identifiers are represented.**

And another hard rule:

> **`minify_names` must be explicitly enabled in `forger.config.py`; it is never enabled implicitly by a production build, CLI mode, or plugin.**

Do not combine name minification with AST tree shaking internally. They should remain independently testable compiler passes.
