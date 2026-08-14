# My Blog

A Django blog application for integration testing Forger.

## Structure

```
realworld/
├── manage.py
├── myblog/              # Django project config
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── accounts/            # User accounts app
│   ├── models.py
│   ├── views.py
│   ├── forms.py
│   ├── urls.py
│   └── admin.py
├── blog/                # Blog app
│   ├── models.py
│   ├── views.py
│   ├── forms.py
│   ├── urls.py
│   └── admin.py
├── templates/           # Django templates
│   ├── base.html
│   ├── blog/
│   └── accounts/
├── static/              # Static files
│   ├── css/
│   └── js/
├── locale/              # Translations
├── forger.config.py     # Forger build config
├── pyproject.toml
└── README.md
```

## Development

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

## Build with Forger

```bash
forger compile
forger build app.forge --target linux-x64
```
