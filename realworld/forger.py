# Forger build configuration for the Django blog project.
#
# This is the imperative-style forger.py.  For Vite-style declarative
# configuration, see forger.config.py instead.

from forger import include, include_module, metadata

# Include all templates
include("templates/**/*")

# Include all static files
include("static/**/*")

# Include locale translations
include("locale/**/*")

# Mark the entry point
metadata("entry_point", "manage.py")
