"""Admin configuration for blog."""

from django.contrib import admin
from .models import Post, Category, Comment


class CommentInline(admin.TabularInline):
    model = Comment
    readonly_fields = ["author", "created"]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "category", "created", "published"]
    list_filter = ["published", "category", "created"]
    prepopulated_fields = {"slug": ("title",)}
    inlines = [CommentInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["author", "post", "created"]
    list_filter = ["created"]
