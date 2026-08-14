"""Blog views."""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import Post, Comment, Category
from .forms import CommentForm


def post_list(request):
    posts = Post.objects.filter(published=True)
    categories = Category.objects.all()
    return render(
        request, "blog/post_list.html", {"posts": posts, "categories": categories}
    )


def post_detail(request, slug):
    post = get_object_or_404(Post, slug=slug, published=True)
    comments = post.comments.select_related("author").all()
    form = CommentForm()
    return render(
        request,
        "blog/post_detail.html",
        {"post": post, "comments": comments, "form": form},
    )


@login_required
def add_comment(request, slug):
    post = get_object_or_404(Post, slug=slug)
    if request.method == "POST":
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.post = post
            comment.author = request.user
            comment.save()
            return redirect("blog:post_detail", slug=slug)
    else:
        form = CommentForm()
    return render(request, "blog/comment_form.html", {"form": form, "post": post})


def category_list(request):
    categories = Category.objects.prefetch_related("posts").all()
    return render(request, "blog/category_list.html", {"categories": categories})


def category_posts(request, slug):
    category = get_object_or_404(Category, slug=slug)
    posts = category.posts.filter(published=True)
    return render(
        request, "blog/category_posts.html", {"category": category, "posts": posts}
    )


def search(request):
    query = request.GET.get("q", "")
    posts = Post.objects.filter(
        Q(title__icontains=query) | Q(body__icontains=query), published=True
    )
    return render(request, "blog/search.html", {"posts": posts, "query": query})
