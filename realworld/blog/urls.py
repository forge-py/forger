"""URL routing for blog app."""

from django.urls import path
from . import views

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="post_list"),
    path("post/<slug:slug>/", views.post_detail, name="post_detail"),
    path("post/<slug:slug>/comment/", views.add_comment, name="add_comment"),
    path("categories/", views.category_list, name="category_list"),
    path("category/<slug:slug>/", views.category_posts, name="category"),
    path("search/", views.search, name="search"),
]
