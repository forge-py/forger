# Forger build configuration for the Django blog project.

from forger import defineConfig

forger_config = defineConfig(
    {
        "entry": "manage.py",
        "project": "myblog",
        "include": [
            "templates/**/*",
            "static/**/*",
            "locale/**/*",
        ],
        "exclude": [
            "**/__pycache__",
            "*.pyc",
            "*.sqlite3",
            "staticfiles/**",
            ".venv/**",
        ],
        "optimizers": {
            "django": {
                "settings_module": "myblog.settings",
            },
        },
        "targets": ["linux-x64", "windows-x64"],
    }
)
