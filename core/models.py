from django.db import models
from django.urls import reverse


class HeroSlide(models.Model):
    """首頁輪播圖"""
    title = models.CharField("標題", max_length=200)
    subtitle = models.CharField("副標題", max_length=300, blank=True)
    image = models.ImageField("圖片", upload_to="hero/", blank=True)
    order = models.IntegerField("排序", default=0)
    is_active = models.BooleanField("啟用", default=True)

    class Meta:
        ordering = ["order"]
        verbose_name = "首頁輪播"
        verbose_name_plural = "首頁輪播"

    def __str__(self):
        return self.title


class Event(models.Model):
    """活動"""
    title = models.CharField("標題", max_length=200)
    slug = models.SlugField("網址代稱", unique=True, allow_unicode=True)
    short_description = models.CharField("簡短描述", max_length=300)
    description = models.TextField("詳細描述")
    date = models.DateTimeField("活動日期")
    location = models.CharField("地點", max_length=200)
    cover_image = models.ImageField("封面圖片", upload_to="events/", blank=True)
    is_featured = models.BooleanField("精選", default=False)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "活動"
        verbose_name_plural = "活動"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("event_detail", kwargs={"slug": self.slug})


class StoryPost(models.Model):
    """故事文章"""
    CATEGORY_CHOICES = [
        ("water_story", "水井故事"),
        ("usr", "USR 成果"),
        ("experience", "體驗活動"),
        ("aiot", "智慧應用"),
    ]

    title = models.CharField("標題", max_length=200)
    slug = models.SlugField("網址代稱", unique=True, allow_unicode=True)
    summary = models.CharField("摘要", max_length=300)
    content = models.TextField("內容")
    image = models.ImageField("圖片", upload_to="stories/", blank=True)
    category = models.CharField("分類", max_length=20, choices=CATEGORY_CHOICES)
    is_featured = models.BooleanField("精選", default=False)
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "故事文章"
        verbose_name_plural = "故事文章"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("story_detail", kwargs={"slug": self.slug})
