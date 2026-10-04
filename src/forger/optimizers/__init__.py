"""Framework-specific optimizer plugins for Forger.

Each module in this package ships a ``BasePlugin`` subclass that
implements a few framework-specific hooks (resource discovery, virtual
modules, etc.). The compiler core knows nothing about Django, Flask,
Jinja, or any other framework — the relevant behavior is provided
by whichever optimizer the project opts into via ``forger.py``.
"""
