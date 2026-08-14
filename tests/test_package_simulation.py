"""Tests against simulated Python packages.

These tests create realistic package structures mimicking real-world
Python packages and verify that Forger correctly analyzes them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from forger.compiler import Compiler

# ====================================================================
# Simulated package: Django-like
# ====================================================================

def test_django_package_structure() -> None:
    """Simulate a Django package with apps, templates, migrations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Project layout:
        # myproject/
        #   manage.py
        #   myproject/
        #     __init__.py
        #     settings.py
        #     urls.py
        #     wsgi.py
        #   accounts/
        #     __init__.py
        #     models.py
        #     views.py
        #     urls.py
        #     admin.py
        #     apps.py
        #     migrations/
        #       __init__.py
        #       0001_initial.py
        #     templates/
        #       accounts/
        #         login.html
        #         register.html
        #     static/
        #       accounts/
        #         style.css

        # manage.py
        (root / "manage.py").write_text(
            "import os\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')\n"
        )

        # myproject/
        proj = root / "myproject"
        proj.mkdir()
        (proj / "__init__.py").write_text("")
        (proj / "settings.py").write_text(
            "INSTALLED_APPS = [\n"
            "    'django.contrib.admin',\n"
            "    'django.contrib.auth',\n"
            "    'accounts',\n"
            "]\n"
            "TEMPLATES = [{'DIRS': ['.'], 'APP_DIRS': True}]\n"
        )
        (proj / "urls.py").write_text(
            "from django.urls import path, include\n"
            "urlpatterns = [path('accounts/', include('accounts.urls'))]\n"
        )
        (proj / "wsgi.py").write_text(
            "from django.core.wsgi import get_wsgi_application\n"
            "application = get_wsgi_application()\n"
        )

        # accounts/
        accounts = root / "accounts"
        accounts.mkdir()
        (accounts / "__init__.py").write_text("")
        (accounts / "models.py").write_text(
            "from django.db import models\n"
            "\n"
            "class User(models.Model):\n"
            "    username = models.CharField(max_length=100)\n"
        )
        (accounts / "views.py").write_text(
            "from django.shortcuts import render\n"
            "from django.http import HttpResponse\n"
            "\n"
            "def login(request): return HttpResponse('ok')\n"
        )
        (accounts / "urls.py").write_text(
            "from django.urls import path\n"
            "from . import views\n"
            "urlpatterns = [path('login/', views.login)]\n"
        )
        (accounts / "admin.py").write_text(
            "from django.contrib import admin\n"
            "from .models import User\n"
            "admin.site.register(User)\n"
        )
        (accounts / "apps.py").write_text(
            "from django.apps import AppConfig\n"
            "\n"
            "class AccountsConfig(AppConfig):\n"
            "    default_auto_field = 'django.db.models.AutoField'\n"
        )

        # migrations/
        migrations = accounts / "migrations"
        migrations.mkdir()
        (migrations / "__init__.py").write_text("")
        (migrations / "0001_initial.py").write_text(
            "from django.db import migrations, models\n"
        )

        # templates/
        acct_templates = accounts / "templates" / "accounts"
        acct_templates.mkdir(parents=True)
        (acct_templates / "login.html").write_text("<form></form>")
        (acct_templates / "register.html").write_text("<form></form>")

        # static/
        acct_static = accounts / "static" / "accounts"
        acct_static.mkdir(parents=True)
        (acct_static / "style.css").write_text("body { }")

        # main entry
        (root / "main.py").write_text(
            "from accounts.models import User\n"
            "from accounts.views import login\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        # Verify analysis
        assert len(compiler.source_files) >= 8
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 5


# ====================================================================
# Simulated package: Flask-like
# ====================================================================

def test_flask_package_structure() -> None:
    """Simulate a Flask package with blueprints and extensions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # app/
        #   __init__.py
        #   routes/
        #     __init__.py
        #     auth.py
        #     api.py
        #   models/
        #     __init__.py
        #     user.py
        #   templates/
        #     base.html
        #     index.html
        #   static/
        #     css/style.css
        #     js/app.js

        app_dir = root / "app"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text(
            "from flask import Flask\n"
            "from flask_sqlalchemy import SQLAlchemy\n"
            "\n"
            "db = SQLAlchemy()\n"
            "\n"
            "def create_app():\n"
            "    app = Flask(__name__)\n"
            "    db.init_app(app)\n"
            "    return app\n"
        )

        routes = app_dir / "routes"
        routes.mkdir()
        (routes / "__init__.py").write_text("")
        (routes / "auth.py").write_text(
            "from flask import Blueprint, render_template, request\n"
            "from flask_login import login_user\n"
            "\n"
            "auth_bp = Blueprint('auth', __name__)\n"
            "\n"
            "@auth_bp.route('/login')\n"
            "def login(): return render_template('login.html')\n"
        )
        (routes / "api.py").write_text(
            "from flask import Blueprint, jsonify\n"
            "import json\n"
            "\n"
            "api_bp = Blueprint('api', __name__)\n"
            "\n"
            "@api_bp.route('/data')\n"
            "def data(): return jsonify({'ok': True})\n"
        )

        models = app_dir / "models"
        models.mkdir()
        (models / "__init__.py").write_text("")
        (models / "user.py").write_text(
            "from app import db\n"
            "from datetime import datetime\n"
            "\n"
            "class User(db.Model):\n"
            "    id = db.Column(db.Integer, primary_key=True)\n"
            "    username = db.Column(db.String(80))\n"
        )

        templates = root / "templates"
        templates.mkdir()
        (templates / "base.html").write_text(
            "<html><body>{% block content %}{% endblock %}</body></html>"
        )
        (templates / "index.html").write_text("{% extends 'base.html' %}")

        static = root / "static"
        css_dir = static / "css"
        js_dir = static / "js"
        css_dir.mkdir(parents=True)
        js_dir.mkdir(parents=True)
        (css_dir / "style.css").write_text("body { margin: 0; }")
        (js_dir / "app.js").write_text("console.log('hello');")

        (root / "main.py").write_text(
            "from app import create_app\n"
            "app = create_app()\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 6
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 4


# ====================================================================
# Simulated package: Data science / ML
# ====================================================================

def test_ml_package_structure() -> None:
    """Simulate a data science / ML project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # src/
        #   __init__.py
        #   data/
        #     __init__.py
        #     loading.py
        #     preprocessing.py
        #   models/
        #     __init__.py
        #     train.py
        #     evaluate.py
        #   utils/
        #     __init__.py
        #     logging.py
        #   config.yaml
        #   requirements.txt

        src = root / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")

        data = src / "data"
        data.mkdir()
        (data / "__init__.py").write_text("")
        (data / "loading.py").write_text(
            "import pandas as pd\n"
            "import numpy as np\n"
            "from pathlib import Path\n"
            "\n"
            "def load_data(path: str) -> pd.DataFrame:\n"
            "    return pd.read_csv(path)\n"
        )
        (data / "preprocessing.py").write_text(
            "import numpy as np\n"
            "from sklearn.preprocessing import StandardScaler\n"
            "\n"
            "def preprocess(X):\n"
            "    scaler = StandardScaler()\n"
            "    return scaler.fit_transform(X)\n"
        )

        models = src / "models"
        models.mkdir()
        (models / "__init__.py").write_text("")
        (models / "train.py").write_text(
            "import numpy as np\n"
            "from sklearn.ensemble import RandomForestClassifier\n"
            "from src.data.loading import load_data\n"
            "\n"
            "def train(X, y):\n"
            "    model = RandomForestClassifier()\n"
            "    model.fit(X, y)\n"
            "    return model\n"
        )
        (models / "evaluate.py").write_text(
            "import numpy as np\n"
            "from sklearn.metrics import accuracy_score, classification_report\n"
            "\n"
            "def evaluate(model, X_test, y_test):\n"
            "    y_pred = model.predict(X_test)\n"
            "    return accuracy_score(y_test, y_pred)\n"
        )

        utils = src / "utils"
        utils.mkdir()
        (utils / "__init__.py").write_text("")
        (utils / "logging.py").write_text(
            "import logging\n"
            "import json\n"
            "\n"
            "def setup_logging():\n"
            "    logging.basicConfig(level=logging.INFO)\n"
        )

        (root / "config.yaml").write_text("model: random_forest\n")

        (root / "main.py").write_text(
            "from src.data.loading import load_data\n"
            "from src.models.train import train\n"
            "from src.models.evaluate import evaluate\n"
            "from src.utils.logging import setup_logging\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 7
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 5


# ====================================================================
# Simulated package: Web scraper
# ====================================================================

def test_scraper_package_structure() -> None:
    """Simulate a web scraping project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # scrapers/
        #   __init__.py
        #   base.py
        #   amazon.py
        #   ebay.py
        # config/
        #   settings.py
        # output/
        #   results.json

        scrapers = root / "scrapers"
        scrapers.mkdir()
        (scrapers / "__init__.py").write_text(
            "from .base import BaseScraper\n"
            "from .amazon import AmazonScraper\n"
            "from .ebay import EbayScraper\n"
        )
        (scrapers / "base.py").write_text(
            "import requests\n"
            "from bs4 import BeautifulSoup\n"
            "import logging\n"
            "\n"
            "class BaseScraper:\n"
            "    def __init__(self, url):\n"
            "        self.url = url\n"
            "    def fetch(self):\n"
            "        response = requests.get(self.url)\n"
            "        return BeautifulSoup(response.text, 'html.parser')\n"
        )
        (scrapers / "amazon.py").write_text(
            "from .base import BaseScraper\n"
            "import json\n"
            "\n"
            "class AmazonScraper(BaseScraper):\n"
            "    def scrape(self):\n"
            "        return []\n"
        )
        (scrapers / "ebay.py").write_text(
            "from .base import BaseScraper\n"
            "import json\n"
            "\n"
            "class EbayScraper(BaseScraper):\n"
            "    def scrape(self):\n"
            "        return []\n"
        )

        config = root / "config"
        config.mkdir()
        (config / "settings.py").write_text(
            "import json\n"
            "\n"
            "SETTINGS = {\n"
            '    "timeout": 30,\n'
            '    "retries": 3,\n'
            "}\n"
        )

        (root / "main.py").write_text(
            "from scrapers import AmazonScraper, EbayScraper\n"
            "from config.settings import SETTINGS\n"
            "import json\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 5
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 4


# ====================================================================
# Simulated package: CLI tool
# ====================================================================

def test_cli_tool_package_structure() -> None:
    """Simulate a CLI tool project."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # cli_tool/
        #   __init__.py
        #   __main__.py
        #   commands/
        #     __init__.py
        #     build.py
        #     deploy.py
        #     test_cmd.py
        #   config/
        #     __init__.py
        #     loader.py
        #   utils/
        #     __init__.py
        #     helpers.py

        tool = root / "cli_tool"
        tool.mkdir()
        (tool / "__init__.py").write_text("__version__ = '1.0.0'\n")
        (tool / "__main__.py").write_text(
            "from click import group\n"
            "from .commands.build import build\n"
            "from .commands.deploy import deploy\n"
            "\n"
            "@group()\n"
            "def main(): pass\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )

        commands = tool / "commands"
        commands.mkdir()
        (commands / "__init__.py").write_text("")
        (commands / "build.py").write_text(
            "from click import command\n"
            "import json\n"
            "import os\n"
            "\n"
            "@command()\n"
            "def build(): pass\n"
        )
        (commands / "deploy.py").write_text(
            "from click import command\n"
            "import shutil\n"
            "\n"
            "@command()\n"
            "def deploy(): pass\n"
        )
        (commands / "test_cmd.py").write_text(
            "from click import command\n"
            "\n"
            "@command()\n"
            "def test_cmd(): pass\n"
        )

        config = tool / "config"
        config.mkdir()
        (config / "__init__.py").write_text("")
        (config / "loader.py").write_text(
            "import yaml\n"
            "import json\n"
            "from pathlib import Path\n"
            "\n"
            "def load_config(path):\n"
            "    with open(path) as f:\n"
            "        return yaml.safe_load(f)\n"
        )

        utils = tool / "utils"
        utils.mkdir()
        (utils / "__init__.py").write_text("")
        (utils / "helpers.py").write_text(
            "import logging\n"
            "import sys\n"
            "\n"
            "def setup_logging():\n"
            "    logging.basicConfig(level=logging.INFO)\n"
        )

        (root / "main.py").write_text(
            "from cli_tool.__main__ import main\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 8
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 5


# ====================================================================
# Simulated package: Plugin architecture
# ====================================================================

def test_plugin_architecture_structure() -> None:
    """Simulate a plugin-based architecture."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # core/
        #   __init__.py
        #   plugin.py
        #   manager.py
        # plugins/
        #   __init__.py
        #   auth_plugin.py
        #   cache_plugin.py
        #   logging_plugin.py

        core = root / "core"
        core.mkdir()
        (core / "__init__.py").write_text("")
        (core / "plugin.py").write_text(
            "from abc import ABC, abstractmethod\n"
            "\n"
            "class Plugin(ABC):\n"
            "    @abstractmethod\n"
            "    def name(self) -> str: pass\n"
            "    @abstractmethod\n"
            "    def initialize(self): pass\n"
        )
        (core / "manager.py").write_text(
            "import importlib\n"
            "from .plugin import Plugin\n"
            "\n"
            "class PluginManager:\n"
            "    def __init__(self):\n"
            "        self.plugins = []\n"
            "    def load(self, module_name):\n"
            "        mod = importlib.import_module(module_name)\n"
            "        return mod\n"
        )

        plugins = root / "plugins"
        plugins.mkdir()
        (plugins / "__init__.py").write_text("")
        (plugins / "auth_plugin.py").write_text(
            "from core.plugin import Plugin\n"
            "\n"
            "class AuthPlugin(Plugin):\n"
            "    def name(self): return 'auth'\n"
            "    def initialize(self): pass\n"
        )
        (plugins / "cache_plugin.py").write_text(
            "from core.plugin import Plugin\n"
            "import json\n"
            "\n"
            "class CachePlugin(Plugin):\n"
            "    def name(self): return 'cache'\n"
            "    def initialize(self): pass\n"
        )
        (plugins / "logging_plugin.py").write_text(
            "from core.plugin import Plugin\n"
            "import logging\n"
            "\n"
            "class LoggingPlugin(Plugin):\n"
            "    def name(self): return 'logging'\n"
            "    def initialize(self): pass\n"
        )

        (root / "main.py").write_text(
            "from core.manager import PluginManager\n"
            "from plugins.auth_plugin import AuthPlugin\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 6
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 4


# ====================================================================
# Simulated package: API server with database
# ====================================================================

def test_api_server_structure() -> None:
    """Simulate an API server with database models and migrations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # api/
        #   __init__.py
        #   app.py
        #   routes/
        #     users.py
        #     items.py
        #   models/
        #     __init__.py
        #     base.py
        #     user.py
        #     item.py
        #   schemas/
        #     __init__.py
        #     user.py
        #   db/
        #     __init__.py
        #     connection.py
        #     migrations/
        #       __init__.py
        #       001_create_users.py

        api = root / "api"
        api.mkdir()
        (api / "__init__.py").write_text("")
        (api / "app.py").write_text(
            "from fastapi import FastAPI\n"
            "from .routes import users, items\n"
            "\n"
            "app = FastAPI()\n"
            "app.include_router(users.router)\n"
            "app.include_router(items.router)\n"
        )

        routes = api / "routes"
        routes.mkdir()
        (routes / "__init__.py").write_text("")
        (routes / "users.py").write_text(
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n"
            "from ..models.user import User\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/users')\n"
            "def list_users(): return []\n"
        )
        (routes / "items.py").write_text(
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/items')\n"
            "def list_items(): return []\n"
        )

        models = api / "models"
        models.mkdir()
        (models / "__init__.py").write_text("")
        (models / "base.py").write_text(
            "from sqlalchemy import Column, Integer\n"
            "from sqlalchemy.orm import DeclarativeBase\n"
            "\n"
            "class Base(DeclarativeBase):\n"
            "    id = Column(Integer, primary_key=True)\n"
        )
        (models / "user.py").write_text(
            "from sqlalchemy import Column, String\n"
            "from .base import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    username = Column(String(80))\n"
        )
        (models / "item.py").write_text(
            "from sqlalchemy import Column, String, Float\n"
            "from .base import Base\n"
            "\n"
            "class Item(Base):\n"
            "    __tablename__ = 'items'\n"
            "    name = Column(String(100))\n"
            "    price = Column(Float)\n"
        )

        schemas = api / "schemas"
        schemas.mkdir()
        (schemas / "__init__.py").write_text("")
        (schemas / "user.py").write_text(
            "from pydantic import BaseModel\n"
            "from typing import Optional\n"
            "\n"
            "class UserSchema(BaseModel):\n"
            "    id: int\n"
            "    username: str\n"
            "    email: Optional[str] = None\n"
        )

        db = api / "db"
        db.mkdir()
        (db / "__init__.py").write_text("")
        (db / "connection.py").write_text(
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import sessionmaker\n"
            "import json\n"
            "\n"
            "engine = create_engine('sqlite:///app.db')\n"
            "Session = sessionmaker(bind=engine)\n"
        )

        migrations = db / "migrations"
        migrations.mkdir()
        (migrations / "__init__.py").write_text("")
        (migrations / "001_create_users.py").write_text(
            "from sqlalchemy import Column, Integer, String\n"
            "\n"
            "def upgrade(): pass\n"
            "def downgrade(): pass\n"
        )

        (root / "main.py").write_text(
            "from api.app import app\n"
            "from api.models.user import User\n"
            "from api.db.connection import Session\n"
        )

        output = root / "app.forge"
        compiler = Compiler(root, "main", output)
        compiler.analyze()

        assert len(compiler.source_files) >= 10
        assert compiler.graph is not None
        assert compiler.graph.node_count() >= 6
