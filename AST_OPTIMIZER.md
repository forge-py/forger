# Implement AST-Level Tree Shaking for the Python Bundler

Implement an **AST-level tree-shaking / reachability pass** for the Python compiler/bundler.

The objective is:

> Starting from the configured entry points, recursively determine which Python definitions are actually referenced and retain only the reachable AST nodes.

This is **symbol-level reachability analysis**, not merely module-level pruning.

The final bundled output should contain only the AST definitions that are required by the reachable program.

---

# 1. Core Concept

Given:

```python
# utils.py

def used():
    return 42

def unused():
    return 999

class UnusedClass:
    ...

CONSTANT_USED = 10
CONSTANT_UNUSED = 20
```

and:

```python
# main.py

from utils import used

print(used())
```

the bundled AST should effectively contain:

```python
def used():
    return 42

print(used())
```

`unused`, `UnusedClass`, and `CONSTANT_UNUSED` should be eliminated.

Do not treat the Python module as an indivisible unit.

---

# 2. Model the Program as a Symbol Graph

Build a graph of:

```text
Module
  ↓
Symbol
  ↓
Reference
  ↓
Symbol
```

For example:

```text
main.main
   │
   └── utils.used
          │
          └── utils.helper
```

Starting from the program roots, perform reachability analysis.

Only reachable symbols survive.

Conceptually:

```text
roots
  ↓
mark reachable symbols
  ↓
follow references
  ↓
mark newly discovered symbols
  ↓
repeat until fixed point
  ↓
remove unreachable AST nodes
```

This should be implemented as a **worklist/fixed-point algorithm**, not recursive AST rewriting.

---

# 3. Roots

The following must be capable of becoming roots:

* entry-point modules
* entry-point functions
* explicitly exported symbols
* explicitly retained symbols
* symbols referenced by generated code
* framework-discovered symbols
* dynamically required symbols

The architecture must allow additional roots to be added later.

For example:

```text
roots:
    main.main
    project.wsgi.application
    explicitly_kept_symbol
```

Do NOT hardcode Django-specific behavior into the generic tree shaker.

---

# 4. Definitions

Track definitions at AST level.

At minimum support:

```python
def foo(): ...
async def foo(): ...

class Foo: ...

x = ...
CONSTANT = ...

import foo
import foo as bar

from foo import bar
from foo import bar as baz
```

Each definition should have a stable internal identity.

Prefer something like:

```text
(module_id, symbol_id)
```

or integer IDs if the compiler already has an internal symbol table.

Do not repeatedly identify symbols by parsing strings.

---

# 5. References

Analyze AST expressions and determine what symbols they reference.

Examples:

```python
foo()
```

references:

```text
foo
```

---

```python
obj.method()
```

references:

```text
obj
```

and potentially:

```text
type(obj).method
```

depending on how sophisticated the resolver is.

---

```python
from utils import foo

foo()
```

creates a reference:

```text
main.foo
    ↓
utils.foo
```

---

```python
import utils

utils.foo()
```

creates:

```text
main.utils
    ↓
utils.foo
```

The tree shaker must distinguish:

* local variables
* imported symbols
* module references
* attributes
* globals
* class members
* function references

Do not simply search for matching identifier strings.

---

# 6. Scope Analysis

Implement proper lexical scope resolution.

The analysis must understand:

```python
x = 1

def foo():
    x = 2
    return x
```

The `x` inside `foo()` refers to the local variable, not the module-level `x`.

Handle:

* module scope
* function scope
* nested functions
* class scope
* comprehensions
* lambda expressions
* exception-handler variables
* assignment targets
* imports
* global declarations
* nonlocal declarations
* function parameters
* class methods

Use a symbol table rather than performing naïve AST name matching.

---

# 7. Function-Level Tree Shaking

If:

```python
def a():
    return 1

def b():
    return a()

def c():
    return 3
```

and only `b()` is reachable:

```text
keep:
    a
    b

remove:
    c
```

The body of every retained function must itself be analyzed for references.

---

# 8. Class-Level Tree Shaking

Classes require special handling.

Given:

```python
class Foo:
    def used(self):
        return 1

    def unused(self):
        return 2
```

If the compiler can prove only `Foo.used` is required, it may retain:

```python
class Foo:
    def used(self):
        return 1
```

However, **do not blindly remove class members when Python semantics make that unsafe**.

Support a conservative mode initially.

Class members may need to remain when:

* accessed dynamically
* discovered through `getattr`
* used through reflection
* required by inheritance
* required by decorators
* required by framework behavior
* required by metaclass behavior
* explicitly retained

Architecture should allow these members to be marked reachable.

---

# 9. Attribute Access

Do NOT interpret:

```python
foo.bar
```

as a simple reference to a globally named `bar`.

Resolve:

```text
foo
```

first.

Then determine whether `bar` is a statically resolvable attribute.

For statically known classes/modules, track the attribute.

For dynamic objects, use conservative retention.

Example:

```python
import utils

utils.used()
```

should allow:

```text
utils.used
```

to become reachable without retaining every symbol in `utils`.

---

# 10. Imports

Imports must be first-class edges in the symbol graph.

For:

```python
from utils import foo
```

create the appropriate binding:

```text
current_module.foo
        ↓
utils.foo
```

For:

```python
import utils
```

the module itself becomes reachable when the import is reachable, but its individual definitions should only survive if referenced.

---

# 11. Side Effects

This is critical for Python.

Do not assume arbitrary module-level code is dead simply because no symbol references it.

For example:

```python
register_plugin()
```

may have observable effects.

Initially implement a conservative policy:

### Always retain

* module-level executable statements with potential side effects
* decorators
* class decorators
* metaclass expressions
* base-class expressions
* default argument expressions
* annotations when configured as runtime-evaluated
* context-manager expressions
* import statements
* explicitly retained constructs

### Potentially eliminate

Pure declarations whose symbols are unreachable:

```python
def unused(): ...
class Unused: ...
CONSTANT = 123
```

provided the compiler can prove their initialization has no observable side effects.

Separate:

```text
symbol reachability
```

from:

```text
statement side-effect analysis
```

Do not mix these concepts into one giant function.

---

# 12. Side-Effect Analysis

Create an extensible purity classification.

For example:

```text
PURE
IMPURE
UNKNOWN
```

Potentially pure:

```python
x = 123
x = "hello"
x = None
```

Potentially impure:

```python
x = foo()
```

Definitely preserve:

```python
register()
open(...)
print(...)
import ...
```

For `UNKNOWN`, be conservative and retain the statement.

Do not attempt aggressive Python purity inference in the first implementation.

---

# 13. Decorators

Decorators are executable code.

For:

```python
@decorator
def foo():
    ...
```

the decorator expression is reachable whenever `foo` is retained.

If the decorator itself is imported:

```python
from decorators import decorator
```

the dependency must propagate to:

```text
decorators.decorator
```

Also account for decorators on classes.

---

# 14. Default Arguments

For:

```python
def foo(x=helper()):
    ...
```

if `foo` survives, `helper` must survive.

The same applies to:

```python
def foo(x=SomeClass()):
```

and other expressions.

---

# 15. Annotations

Respect Python annotation semantics.

The implementation must account for whether annotations are:

* evaluated at runtime
* deferred
* stringized
* explicitly configured by the compiler

Do not incorrectly remove symbols referenced by runtime annotations.

---

# 16. Nested Functions and Closures

Handle:

```python
def outer():
    x = 10

    def inner():
        return x

    return inner
```

If `outer` survives and `inner` is returned/referenced, preserve the necessary closure variables.

Do not eliminate variables merely because they aren't global symbols.

---

# 17. Globals / Nonlocal

Correctly handle:

```python
x = 10

def foo():
    global x
    return x
```

`foo` creates a dependency on module-level `x`.

Likewise:

```python
def outer():
    x = 10

    def inner():
        nonlocal x
        x += 1
```

The enclosing binding must remain.

---

# 18. Control Flow

References must be discovered throughout AST control flow:

```python
if condition:
    foo()
else:
    bar()
```

Both `foo` and `bar` are conservatively considered reachable unless the compiler has a separate constant-folding pass capable of proving one branch unreachable.

Do not perform speculative control-flow optimization here.

---

# 19. Fixed-Point Algorithm

The implementation should conceptually look like:

```text
initialize roots

while worklist is not empty:

    symbol = worklist.pop()

    analyze symbol AST

    for each referenced symbol:
        if not reachable:
            mark reachable
            push onto worklist
```

Continue until no new symbols are discovered.

Then perform a separate AST reconstruction/pruning pass.

Do not mutate the AST during reachability analysis.

---

# 20. Separate Analysis From Transformation

Use two distinct phases:

```text
Phase 1:
AST
 ↓
symbol table
 ↓
reference graph
 ↓
reachability analysis
 ↓
reachable symbol set

Phase 2:
AST + reachable symbol set
 ↓
pruned AST
```

This separation is mandatory.

The analysis should be deterministic and independently testable.

---

# 21. Preserve AST Structure

When pruning:

* preserve source locations where possible
* preserve decorators for retained definitions
* preserve required imports
* preserve required statements
* preserve ordering
* preserve scope semantics

Do not emit malformed ASTs.

The result must remain valid Python AST that can be compiled by CPython where supported by the compiler's target Python version.

---

# 22. Conservative Dynamic Features

Python has many dynamic mechanisms:

```python
getattr(...)
setattr(...)
globals()
locals()
vars()
eval(...)
exec(...)
__import__(...)
importlib.import_module(...)
```

When static analysis cannot prove what is accessed, **do not aggressively prune**.

Introduce an escape hatch such as:

```text
DYNAMIC_ACCESS
```

which can conservatively mark a module, class, or symbol as retained.

The design should make it possible to progressively improve precision later.

---

# 23. Django / Framework Integration

The generic tree shaker must NOT contain framework-specific assumptions.

Instead expose hooks such as:

```text
add_root(symbol)
retain_module(module)
retain_symbol(symbol)
mark_dynamic(symbol)
```

A Django analyzer can later discover things such as:

```text
URL patterns
models
admin registrations
AppConfig
middleware
signal handlers
template references
management commands
```

and add them as roots.

---

# 24. Performance

This is a core compiler pass and must be **extremely fast**.

Avoid:

* repeatedly walking the entire AST
* repeated symbol-name lookups
* recursive dependency traversal
* rebuilding symbol tables for every reference
* O(N²) scans
* unnecessary Python object allocation

Prefer:

* integer IDs
* indexed symbol tables
* compact adjacency/reference lists
* iterative worklists
* one-time AST indexing
* cached scope resolution

If a performance-critical component would benefit substantially from Rust, identify it explicitly and structure the implementation so it can later be moved to Rust.

Do not introduce unnecessary dependencies.

---

# 25. Tests

Create tests covering at least:

### Unused functions

```python
def used(): ...
def unused(): ...

used()
```

Only `used` survives.

### Transitive dependencies

```python
def a():
    return b()

def b():
    return c()

def c():
    return 42

def unused():
    ...
```

Keep `a`, `b`, `c`.

### Imported symbol

```python
from utils import used
used()
```

Only `utils.used` and its dependencies survive.

### Aliased import

```python
from utils import used as foo
foo()
```

### Module import

```python
import utils
utils.used()
```

### Multiple entry points

Verify the union of reachable symbols survives.

### Circular dependencies

Verify termination and correctness.

### Nested functions

### Closures

### `global`

### `nonlocal`

### decorators

### default arguments

### annotations

### class methods

### inheritance

### comprehensions

### lambdas

### side-effectful module-level statements

### dynamic access

### `getattr`

### `globals`

### `exec`

### `eval`

### unused imports

### unused classes

### unused constants

---

# 26. Benchmarks

Add benchmarks for:

```text
1,000 modules
10,000 modules
100,000 symbols
1,000,000 references
```

Measure:

```text
AST indexing
symbol resolution
reference analysis
reachability
AST pruning
total time
memory usage
```

The algorithm should scale approximately linearly with the number of AST nodes and references.

---

# 27. Deliverable

Implement this as a clean compiler pass named something equivalent to:

```text
ASTTreeShaker
```

or:

```text
SymbolReachabilityAnalyzer
```

with a separate:

```text
ASTPruner
```

Do not redesign the compiler.

First inspect the existing architecture and integrate with the existing:

* parser
* AST representation
* module resolver
* symbol table
* module graph
* compiler pipeline

Then implement the minimum architectural changes necessary.

The fundamental invariant is:

> **Every emitted definition must be reachable from a root through statically known symbol references or explicitly retained by the conservative safety model.**

And:

> **Never remove code merely because a textual name appears unused. Perform actual scope-aware symbol/reference analysis.**
